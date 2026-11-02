"""Pantalla de una terminal: interpreta lo que escribe un programa (texto y secuencias VT/ANSI). Sin Qt, para poder probarlo sin interfaz."""
from __future__ import annotations

import codecs
import re
import unicodedata
from collections import deque
from collections.abc import Callable
from typing import NamedTuple

HISTORY_LINES = 5000
_MAX_SEQUENCE = 256  # una secuencia de control no mide más: lo que pase de aquí no se interpreta
_MAX_COUNT = 65535
_TEXT_RUN = re.compile(r"[^\x00-\x1f\x7f-\x9f]+")
# Las líneas y esquinas del juego de caracteres gráfico (ESC ( 0).
_DEC_GRAPHICS = dict(zip("`afgjklmnopqrstuvwxyz{|}~", "◆▒°±┘┐┌└┼⎺⎻─⎼⎽├┤┴┬│≤≥π≠£·"))


class Style(NamedTuple):
    """Colores: None es el predeterminado, un entero un color de la paleta de 256 y una tupla un RGB."""
    fg: object = None
    bg: object = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    inverse: bool = False
    strike: bool = False


DEFAULT_STYLE = Style()
BLANK = (" ", DEFAULT_STYLE)
_WIDE_TAIL = ""  # la segunda celda de un carácter de ancho doble


class Line(list):
    """Las celdas (carácter, estilo) de una fila. `wrapped`: el texto sigue en la fila siguiente."""
    __slots__ = ("wrapped",)

    def __init__(self, cells=()) -> None:
        super().__init__(cells)
        self.wrapped = False


def char_width(char: str) -> int:
    if char < "̀":
        return 1
    if unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


class Screen:
    """Las filas visibles y las que ya subieron. `respond` recibe lo que la terminal contesta al programa."""

    def __init__(self, cols: int = 80, rows: int = 24, respond: Callable[[bytes], None] | None = None, history: int = HISTORY_LINES) -> None:
        self.cols = max(1, cols)
        self.rows = max(1, rows)
        self.respond = respond or (lambda _data: None)
        self.history: deque[Line] = deque(maxlen=history)
        self.dropped = 0  # filas que ya no caben en el historial: mantiene estable el número de cada fila
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._reset()

    def _reset(self) -> None:
        self.lines = [self._new_line(DEFAULT_STYLE) for _row in range(self.rows)]
        self.alt = False
        self.style = DEFAULT_STYLE
        self.x = self.y = 0
        self.cursor_visible = True
        self.app_cursor = False
        self.bracketed_paste = False
        self.autowrap = True
        self.insert_mode = False
        self.origin_mode = False
        self._pending_wrap = False
        self._graphics = False
        self._top, self._bottom = 0, self.rows - 1
        self._tabs = set(range(0, self.cols, 8))
        self._saved_cursor: tuple | None = None
        self._main_lines: list[Line] | None = None
        self._last_char = " "
        self._state = None
        self._buffer = ""

    # ------------------------------------------------------------------ lectura
    @property
    def line_count(self) -> int:
        return len(self.history) + self.rows

    @property
    def cursor_index(self) -> int:
        return len(self.history) + self.y

    def line(self, index: int) -> Line:
        """Una fila contando desde la más antigua del historial."""
        filed = len(self.history)
        return self.history[index] if index < filed else self.lines[index - filed]

    def before_cursor(self) -> str:
        """Lo escrito en la fila del cursor hasta él, sin los espacios del final. En la última columna incluye su celda: al escribirla el cursor no
        avanza, y si la consola lo recoloca, por ejemplo al cambiar de tamaño, ya no se sabe que estaba detrás."""
        end = self.x + 1 if self._pending_wrap or self.x == self.cols - 1 else self.x
        return "".join(cell[0] for cell in self.lines[self.y][:end]).rstrip()

    def line_before_cursor(self) -> str:
        """Como `before_cursor`, con el principio de la línea si es tan larga que viene de filas anteriores."""
        parts = [self.before_cursor()]
        index = self.cursor_index - 1
        while index >= 0 and self.line(index).wrapped:
            parts.insert(0, "".join(cell[0] for cell in self.line(index)))
            index -= 1
        return "".join(parts)

    def display(self) -> list[str]:
        return ["".join(cell[0] for cell in line).rstrip() for line in self.lines]

    def text_between(self, start: tuple[int, int], end: tuple[int, int]) -> str:
        """El texto de una selección, de (fila, columna) a (fila, columna) sin incluirla. Las filas cuentan desde `dropped`."""
        parts: list[str] = []
        first, last = start[0] - self.dropped, end[0] - self.dropped
        for index in range(max(0, first), min(last, self.line_count - 1) + 1):
            line = self.line(index)
            text = "".join(cell[0] for cell in line[start[1] if index == first else 0:end[1] if index == last else None])
            joined = line.wrapped and index != last
            parts.append(text if joined else text.rstrip() + ("" if index == last else "\n"))
        return "".join(parts)

    # ------------------------------------------------------------------ entrada
    def feed(self, data: bytes) -> None:
        self.feed_text(self._decoder.decode(data))

    def feed_text(self, text: str) -> None:
        position, size = 0, len(text)
        while position < size:
            if self._state is None:
                run = _TEXT_RUN.match(text, position)
                if run:
                    self._put_text(run.group())
                    position = run.end()
                    continue
                self._control(text[position])
            else:
                self._state(text[position])
            position += 1

    def _control(self, char: str) -> None:
        if char == "\x1b":
            self._state = self._escape
        elif char == "\r":
            self.x = 0
            self._pending_wrap = False
        elif char in "\n\x0b\x0c":
            self._index()
        elif char == "\b":
            self.x = max(0, self.x - 1)
            self._pending_wrap = False
        elif char == "\t":
            self._tab(1)
        elif char == "\x9b":
            self._start_csi()

    def _start_csi(self) -> None:
        self._buffer = ""
        self._state = self._csi

    def _escape(self, char: str) -> None:
        self._state = None
        if char == "[":
            self._start_csi()
        elif char in "]PX^_":  # títulos de ventana y otros mensajes: no cambian lo que se ve
            self._state = self._ignored_string
        elif char in "()*+":
            self._state = self._charset if char == "(" else self._skip_one
        elif char in "#% ":
            self._state = self._skip_one
        elif char == "7":
            self._save_cursor()
        elif char == "8":
            self._restore_cursor()
        elif char == "D":
            self._index()
        elif char == "E":
            self.x = 0
            self._index()
        elif char == "H":
            self._tabs.add(self.x)
        elif char == "M":
            self._reverse_index()
        elif char == "c":
            self._reset()

    def _skip_one(self, _char: str) -> None:
        self._state = None

    def _charset(self, char: str) -> None:
        self._graphics = char == "0"
        self._state = None

    def _csi(self, char: str) -> None:
        if "\x20" <= char <= "\x3f":
            if len(self._buffer) <= _MAX_SEQUENCE:
                self._buffer += char
        elif "\x40" <= char <= "\x7e":
            self._state = None
            self._dispatch_csi(char)
        elif char == "\x1b":
            self._state = self._escape
        elif char in "\x18\x1a":
            self._state = None
        else:
            self._control(char)  # los caracteres de control también valen en mitad de una secuencia

    def _ignored_string(self, char: str) -> None:
        if char == "\x07":
            self._state = None
        elif char == "\x1b":
            self._state = self._skip_one

    # ------------------------------------------------------------------ texto
    def _new_line(self, style: Style | None = None) -> Line:
        return Line([self._blank(style)] * self.cols)

    def _blank(self, style: Style | None = None) -> tuple:
        """Lo borrado conserva el color de fondo actual."""
        style = self.style if style is None else style
        return BLANK if style.bg is None else (" ", Style(bg=style.bg))

    def _clear_wide(self, line: Line, x: int) -> None:
        """Escribir sobre media letra de ancho doble borra la otra mitad."""
        if line[x][0] == _WIDE_TAIL and x > 0:
            line[x - 1] = BLANK
        if x + 1 < self.cols and line[x + 1][0] == _WIDE_TAIL:
            line[x + 1] = BLANK

    def _put_text(self, text: str) -> None:
        cols, style = self.cols, self.style
        for char in text:
            if self._graphics:
                char = _DEC_GRAPHICS.get(char, char)
            width = char_width(char)
            if width == 0:
                self._combine(char)
                continue
            if self._pending_wrap or self.x + width > cols:
                self._pending_wrap = False
                if self.autowrap:
                    self.lines[self.y].wrapped = True
                    self.x = 0
                    self._index()
                else:
                    self.x = max(0, cols - width)
            line = self.lines[self.y]
            if self.insert_mode:
                line[self.x:self.x] = [BLANK] * width
                del line[cols:]
            self._clear_wide(line, self.x)
            line[self.x] = (char, style)
            if width == 2 and self.x + 1 < cols:
                self._clear_wide(line, self.x + 1)
                line[self.x + 1] = (_WIDE_TAIL, style)
            self._last_char = char
            self.x += width
            if self.x >= cols:
                self.x = cols - 1
                self._pending_wrap = self.autowrap

    def _combine(self, char: str) -> None:
        line = self.lines[self.y]
        x = self.x if self._pending_wrap else self.x - 1
        if x > 0 and line[x][0] == _WIDE_TAIL:
            x -= 1
        if x >= 0:
            line[x] = (line[x][0] + char, line[x][1])

    # ------------------------------------------------------------------ cursor y desplazamiento
    def _index(self) -> None:
        if self.y == self._bottom:
            self._scroll_up(1)
        elif self.y < self.rows - 1:
            self.y += 1
        self._pending_wrap = False

    def _reverse_index(self) -> None:
        if self.y == self._top:
            self._scroll_down(1)
        elif self.y > 0:
            self.y -= 1
        self._pending_wrap = False

    def _scroll_up(self, count: int) -> None:
        for _time in range(min(count, self._bottom - self._top + 1)):
            line = self.lines.pop(self._top)
            if self._top == 0 and not self.alt:
                if len(self.history) == self.history.maxlen:
                    self.dropped += 1
                self.history.append(line)
            self.lines.insert(self._bottom, self._new_line())

    def _scroll_down(self, count: int) -> None:
        for _time in range(min(count, self._bottom - self._top + 1)):
            del self.lines[self._bottom]
            self.lines.insert(self._top, self._new_line())

    def _tab(self, count: int) -> None:
        for _time in range(count):
            self.x = next((stop for stop in sorted(self._tabs) if stop > self.x), self.cols - 1)
        self._pending_wrap = False

    def _back_tab(self, count: int) -> None:
        for _time in range(count):
            self.x = next((stop for stop in sorted(self._tabs, reverse=True) if stop < self.x), 0)
        self._pending_wrap = False

    def _move_to(self, x: int, y: int) -> None:
        self.x = min(max(0, x), self.cols - 1)
        self.y = min(max(0, y), self.rows - 1)
        self._pending_wrap = False

    def _save_cursor(self) -> None:
        self._saved_cursor = (self.x, self.y, self.style, self._graphics, self._pending_wrap)

    def _restore_cursor(self) -> None:
        if self._saved_cursor:
            x, y, self.style, self._graphics, pending = self._saved_cursor
            self._move_to(x, y)
            self._pending_wrap = pending

    # ------------------------------------------------------------------ secuencias CSI
    def _dispatch_csi(self, final: str) -> None:
        raw = self._buffer
        if len(raw) > _MAX_SEQUENCE:
            return
        private = raw[0] if raw[:1] in ("?", ">", "=", "<") else ""
        raw = raw[len(private):]
        if " " in raw or "!" in raw or '"' in raw or "$" in raw or "'" in raw:
            return  # estilo del cursor y otros ajustes que no cambian el contenido
        groups = [[int(part) if part.isdigit() else None for part in group.split(":")] for group in raw.split(";")] if raw else []
        params = [group[0] for group in groups]

        def count(position: int = 0) -> int:
            value = params[position] if position < len(params) else None
            return min(value or 1, _MAX_COUNT)

        def value(position: int = 0) -> int:
            return (params[position] if position < len(params) else None) or 0

        if private == "?":
            if final in "hl":
                self._set_private_modes(params, final == "h")
            elif final == "n" and value() == 6:
                self.respond(f"\x1b[?{self.y + 1};{self.x + 1}R".encode())
            return
        if private == ">":
            if final == "c":
                self.respond(b"\x1b[>0;10;1c")
            return
        if private:
            return
        if final == "A":
            self._move_to(self.x, max(self.y - count(), self._top if self.y >= self._top else 0))
        elif final in "Be":
            self._move_to(self.x, min(self.y + count(), self._bottom if self.y <= self._bottom else self.rows - 1))
        elif final in "Ca":
            self._move_to(self.x + count(), self.y)
        elif final == "D":
            self._move_to(self.x - count(), self.y)
        elif final == "E":
            self._move_to(0, min(self.y + count(), self._bottom if self.y <= self._bottom else self.rows - 1))
        elif final == "F":
            self._move_to(0, max(self.y - count(), self._top if self.y >= self._top else 0))
        elif final in "G`":
            self._move_to(count() - 1, self.y)
        elif final == "d":
            self._move_to(self.x, self._row_from(count()))
        elif final in "Hf":
            self._move_to(count(1) - 1, self._row_from(count()))
        elif final == "I":
            self._tab(count())
        elif final == "Z":
            self._back_tab(count())
        elif final == "J":
            self._erase_display(value())
        elif final == "K":
            self._erase_line(value())
        elif final == "L":
            self._insert_lines(count())
        elif final == "M":
            self._delete_lines(count())
        elif final == "@":
            line = self.lines[self.y]
            line[self.x:self.x] = [self._blank()] * count()
            del line[self.cols:]
        elif final == "P":
            line = self.lines[self.y]
            del line[self.x:self.x + count()]
            line.extend([self._blank()] * (self.cols - len(line)))
        elif final == "X":
            end = min(self.x + count(), self.cols)
            self.lines[self.y][self.x:end] = [self._blank()] * (end - self.x)
        elif final == "S":
            self._scroll_up(count())
        elif final == "T":
            self._scroll_down(count())
        elif final == "b":
            self._put_text(self._last_char * count())
        elif final == "c":
            self.respond(b"\x1b[?1;2c")
        elif final == "g":
            if value() == 0:
                self._tabs.discard(self.x)
            elif value() == 3:
                self._tabs.clear()
        elif final in "hl":
            if 4 in params:
                self.insert_mode = final == "h"
        elif final == "m":
            self._select_style(groups)
        elif final == "n":
            if value() == 5:
                self.respond(b"\x1b[0n")
            elif value() == 6:
                self.respond(f"\x1b[{self.y + 1};{self.x + 1}R".encode())
        elif final == "r":
            top, bottom = count() - 1, (params[1] if len(params) > 1 and params[1] else self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self._top, self._bottom = top, bottom
                self._move_to(0, top if self.origin_mode else 0)
        elif final == "s":
            self._save_cursor()
        elif final == "u":
            self._restore_cursor()

    def _row_from(self, row: int) -> int:
        return min(self._top + row - 1, self._bottom) if self.origin_mode else row - 1

    def _set_private_modes(self, modes: list, on: bool) -> None:
        for mode in modes:
            if mode == 1:
                self.app_cursor = on
            elif mode == 6:
                self.origin_mode = on
                self._move_to(0, self._top if on else 0)
            elif mode == 7:
                self.autowrap = on
                self._pending_wrap = self._pending_wrap and on
            elif mode == 25:
                self.cursor_visible = on
            elif mode in (47, 1047, 1049):
                self._set_alt_screen(on, save_cursor=mode == 1049)
            elif mode == 2004:
                self.bracketed_paste = on

    def _set_alt_screen(self, on: bool, save_cursor: bool) -> None:
        """Los programas a pantalla completa no dejan rastro: al salir vuelve lo que había."""
        if on == self.alt:
            return
        if on:
            if save_cursor:
                self._save_cursor()
            self._main_lines = self.lines
            self.lines = [self._new_line(DEFAULT_STYLE) for _row in range(self.rows)]
        else:
            self.lines = self._main_lines or [self._new_line(DEFAULT_STYLE) for _row in range(self.rows)]
            self._main_lines = None
        self.alt = on
        self._top, self._bottom = 0, self.rows - 1
        if save_cursor and not on:
            self._restore_cursor()

    def _erase_line(self, mode: int) -> None:
        line = self.lines[self.y]
        start, end = {0: (self.x, self.cols), 1: (0, self.x + 1), 2: (0, self.cols)}.get(mode, (0, 0))
        line[start:end] = [self._blank()] * (end - start)
        if end == self.cols:
            line.wrapped = False

    def _erase_display(self, mode: int) -> None:
        if mode == 3:
            self.dropped += len(self.history)
            self.history.clear()
        elif mode == 2:
            self.lines[:] = [self._new_line() for _row in range(self.rows)]
        elif mode == 0:
            self._erase_line(0)
            self.lines[self.y + 1:] = [self._new_line() for _row in range(self.y + 1, self.rows)]
        elif mode == 1:
            self._erase_line(1)
            self.lines[:self.y] = [self._new_line() for _row in range(self.y)]

    def _insert_lines(self, count: int) -> None:
        if self._top <= self.y <= self._bottom:
            for _time in range(min(count, self._bottom - self.y + 1)):
                del self.lines[self._bottom]
                self.lines.insert(self.y, self._new_line())
            self._move_to(0, self.y)

    def _delete_lines(self, count: int) -> None:
        if self._top <= self.y <= self._bottom:
            for _time in range(min(count, self._bottom - self.y + 1)):
                del self.lines[self.y]
                self.lines.insert(self._bottom, self._new_line())
            self._move_to(0, self.y)

    def _select_style(self, groups: list[list]) -> None:
        style = self.style
        queue = list(groups) or [[0]]
        while queue:
            group = queue.pop(0)
            code = group[0] or 0
            if code == 0:
                style = DEFAULT_STYLE
            elif code in _STYLE_FLAGS:
                field, on = _STYLE_FLAGS[code]
                style = style._replace(**{field: on})
            elif code == 22:
                style = style._replace(bold=False, dim=False)
            elif 30 <= code <= 37:
                style = style._replace(fg=code - 30)
            elif 40 <= code <= 47:
                style = style._replace(bg=code - 40)
            elif 90 <= code <= 97:
                style = style._replace(fg=code - 82)
            elif 100 <= code <= 107:
                style = style._replace(bg=code - 92)
            elif code == 39:
                style = style._replace(fg=None)
            elif code == 49:
                style = style._replace(bg=None)
            elif code in (38, 48):
                color = _extended_color(group, queue)
                if color is not None:
                    style = style._replace(**{"fg" if code == 38 else "bg": color})
        self.style = style

    # ------------------------------------------------------------------ tamaño
    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(1, cols), max(1, rows)
        if (cols, rows) == (self.cols, self.rows):
            return
        if self._main_lines is not None:
            self._main_lines = self._fit_lines(self._main_lines, cols, rows)
        self.y = self._fit_rows(self.lines, rows, keep_history=not self.alt)
        self.lines = self._fit_lines(self.lines, cols, rows)
        self.cols, self.rows = cols, rows
        self._top, self._bottom = 0, rows - 1
        self._tabs = {stop for stop in self._tabs if stop < cols} | set(range(0, cols, 8))
        self.x = min(self.x, cols - 1)
        self._pending_wrap = False

    def _fit_rows(self, lines: list[Line], rows: int, keep_history: bool) -> int:
        """Si faltan filas se quitan las vacías de abajo y luego, para no perder el cursor, las de arriba. Devuelve la fila del cursor."""
        y = self.y
        while len(lines) > rows and len(lines) - 1 > y and not "".join(cell[0] for cell in lines[-1]).strip():
            lines.pop()
        while len(lines) > rows:
            line = lines.pop(0)
            y = max(0, y - 1)
            if keep_history:
                if len(self.history) == self.history.maxlen:
                    self.dropped += 1
                self.history.append(line)
        return min(y, rows - 1)

    def _fit_lines(self, lines: list[Line], cols: int, rows: int) -> list[Line]:
        lines = lines[-rows:]
        for line in lines:
            del line[cols:]
            line.extend([BLANK] * (cols - len(line)))
            if line and line[-1][0] != _WIDE_TAIL and line[-1][0] != " " and char_width(line[-1][0][0]) == 2:
                line[-1] = BLANK  # solo cabía media letra de ancho doble
        lines.extend(Line([BLANK] * cols) for _row in range(rows - len(lines)))
        return lines


_STYLE_FLAGS = {1: ("bold", True), 2: ("dim", True), 3: ("italic", True), 4: ("underline", True), 7: ("inverse", True), 9: ("strike", True),
                23: ("italic", False), 24: ("underline", False), 27: ("inverse", False), 29: ("strike", False)}


def _extended_color(group: list, queue: list):
    """38;5;n y 38;2;r;g;b, separados por punto y coma o por dos puntos."""
    parts = list(group[1:])
    if not parts:
        parts = [item[0] for item in queue[:1]]
        kind = parts[0] if parts else None
        needed = {5: 1, 2: 3}.get(kind, 0)
        parts += [item[0] for item in queue[1:1 + needed]]
        del queue[:1 + needed]
    kind = parts[0] if parts else None
    if kind == 5 and len(parts) >= 2 and parts[1] is not None:
        return min(parts[1], 255)
    if kind == 2:
        channels = [part or 0 for part in parts[1:]][-3:]  # con dos puntos puede ir antes un espacio de color vacío
        if len(channels) == 3:
            return tuple(min(channel, 255) for channel in channels)
    return None
