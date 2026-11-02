"""Ventana de progreso al copiar o mover: dos barras, tiempos, gráfica de velocidad y botones para omitir, pausar y cancelar."""
from __future__ import annotations

import os
import time
from collections import deque

from PySide6.QtCore import QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from .. import APP_NAME
from ..core.listing import format_size
from . import theme
from ..core.i18n import tr

SHOW_AFTER_MS = 300  # lo que termina enseguida no llega a abrir la ventana
TICK_MS = 100
SPEED_WINDOW_S = 1.5
GRAPH_SAMPLES = 260
GRAPH_STEP_PX = 2


def format_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


def format_speed(bytes_per_second: float) -> str:
    return f"{format_size(int(bytes_per_second))}/s"


def _path_rows(*rows: tuple[str, QLabel]) -> QVBoxLayout:
    column = QVBoxLayout()
    column.setSpacing(2)
    for title, value in rows:
        row = QHBoxLayout()
        caption = QLabel(title)
        caption.setFixedWidth(28)
        row.addWidget(caption)
        row.addWidget(value, 1)
        column.addLayout(row)
    return column


class SpeedGraph(QWidget):
    """Velocidad reciente, de derecha a izquierda. La línea blanca es la media desde el principio."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.samples: deque[float] = deque(maxlen=GRAPH_SAMPLES)
        self.average = 0.0
        self.setMinimumHeight(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def add_sample(self, speed: float, average: float) -> None:
        self.samples.append(speed)
        self.average = average
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        area = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(area, QColor("black"))
        painter.setPen(QColor(theme.current().border))
        painter.drawRect(area)
        if not self.samples:
            return
        top = max(max(self.samples), self.average, 1.0) * 1.25
        y_of = lambda value: area.bottom() - value / top * (area.height() - 2)
        points = [QPointF(area.right(), area.bottom())]
        for position, value in enumerate(reversed(self.samples)):
            x = area.right() - position * GRAPH_STEP_PX
            if x < area.left():
                break
            points.append(QPointF(x, y_of(value)))
        points.append(QPointF(points[-1].x(), area.bottom()))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#37d14a"))
        painter.setBrush(QColor(12, 110, 28))
        painter.drawPolygon(QPolygonF(points))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        line = round(y_of(self.average))
        painter.setPen(QColor("white"))
        painter.drawLine(area.left() + 1, line, area.right() - 1, line)
        height = painter.fontMetrics().height()
        painter.setPen(QColor("#a8f5e8"))
        text_y = line + 2 if line + 2 + height < area.bottom() else line - height - 2
        painter.drawText(QRect(area.left() + 4, text_y, area.width() // 2, height), Qt.AlignmentFlag.AlignLeft, format_speed(self.average))
        current_y = max(area.top() + 2, round(y_of(self.samples[-1])) - height - 2)
        painter.drawText(QRect(area.left(), current_y, area.width() - 4, height), Qt.AlignmentFlag.AlignRight, format_speed(self.samples[-1]))


class TransferDialog(QDialog):
    cancelRequested = Signal()

    def __init__(self, verb: str, source: str, target: str, sink, parent=None) -> None:
        super().__init__(parent)
        self.verb, self.sink = verb, sink
        self._source, self._target = source, target
        self._finished = False
        self._elapsed = 0.0
        self._last_tick: float | None = None
        self._history: deque[tuple[float, int]] = deque()
        self._name = ""
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowTitleHint | Qt.WindowType.CustomizeWindowHint |
                            Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowTitle(f"{verb} - {APP_NAME}")
        self.resize(560, 390)

        self.from_label, self.to_label = self._elided_label(), self._elided_label()
        self.current_label = self._elided_label()
        self.current_bar = self._bar()
        self.current_size = QLabel(alignment=Qt.AlignmentFlag.AlignRight)
        self.items_label = QLabel()
        self.elapsed_label = QLabel()
        self.remaining_label = QLabel(alignment=Qt.AlignmentFlag.AlignRight)
        self.total_bar = self._bar()
        self.graph = SpeedGraph()
        self.skip_button = QPushButton(tr("Omitir"))
        self.skip_button.setToolTip(tr("Deja este archivo sin copiar y sigue con el siguiente"))
        self.skip_button.clicked.connect(self.skip)
        self.pause_button = QPushButton(tr("Pausar"))
        self.pause_button.clicked.connect(self.toggle_pause)
        self.unattended = QCheckBox(tr("Desatendido"))
        self.unattended.setToolTip(tr("Si hay errores, solo se anotan en el registro: al terminar no se muestra ningún aviso"))
        self.cancel_button = QPushButton(tr("Cancelar"))
        self.cancel_button.clicked.connect(self.cancel)
        for button in (self.skip_button, self.pause_button, self.cancel_button):
            button.setAutoDefault(False)  # si no, Intro omitiría un archivo sin querer

        paths = _path_rows((tr("De:"), self.from_label), (tr("A:"), self.to_label))
        top_line = QFrame()
        top_line.setFrameShape(QFrame.Shape.HLine)
        top_line.setStyleSheet(f"color: {theme.current().border};")
        size_row = QHBoxLayout()
        size_row.addStretch(1)
        size_row.addWidget(self.current_size)
        times = QHBoxLayout()
        times.addWidget(self.items_label)
        times.addStretch(1)
        times.addWidget(self.elapsed_label)
        times.addSpacing(40)
        times.addWidget(self.remaining_label)
        buttons = QHBoxLayout()
        buttons.addWidget(self.skip_button)
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.unattended)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        layout.addLayout(paths)
        layout.addSpacing(4)
        layout.addWidget(top_line)
        layout.addSpacing(4)
        layout.addWidget(self.current_label)
        layout.addWidget(self.current_bar)
        layout.addLayout(size_row)
        layout.addSpacing(4)
        layout.addLayout(times)
        layout.addWidget(self.total_bar)
        layout.addSpacing(6)
        layout.addWidget(self.graph, 1)
        layout.addSpacing(6)
        layout.addLayout(buttons)

        self._tick_timer = QTimer(self, interval=TICK_MS)
        self._tick_timer.timeout.connect(self.tick)
        self._tick_timer.start()
        self._show_timer = QTimer(self, singleShot=True, interval=SHOW_AFTER_MS)
        self._show_timer.timeout.connect(self._show_now)
        self._show_timer.start()
        self.tick()

    @staticmethod
    def _elided_label() -> QLabel:
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return label

    @staticmethod
    def _bar() -> QProgressBar:
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setTextVisible(False)
        bar.setFixedHeight(16)
        colors = theme.current()
        bar.setStyleSheet(f"QProgressBar {{ background: {colors.field}; border: 1px solid {colors.border}; border-radius: 0; }}"
                          "QProgressBar::chunk { background: #0f9a24; }")
        return bar

    def _show_now(self) -> None:
        if not self._finished:
            self.show()

    def _set_elided(self, label: QLabel, text: str, mode=Qt.TextElideMode.ElideMiddle) -> None:
        label.setToolTip(text)
        label.setText(label.fontMetrics().elidedText(text, mode, max(60, label.width())))

    def _speed(self, now: float, done: int) -> float:
        """Velocidad de los últimos instantes: la de cada momento varía demasiado para leerla."""
        self._history.append((now, done))
        while len(self._history) > 2 and now - self._history[0][0] > SPEED_WINDOW_S:
            self._history.popleft()
        then, before = self._history[0]
        return max(0.0, (done - before) / (now - then)) if now > then else 0.0

    def tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._set_elided(self.from_label, self._source)
        self._set_elided(self.to_label, self._target)
        state = self.sink.transfer
        if state is None or not state.ready:
            self.current_label.setText(tr("Preparando…"))
            self.current_bar.setRange(0, 0)
            self.total_bar.setRange(0, 0)
            self.skip_button.setEnabled(False)
            return
        self.current_bar.setRange(0, 1000)
        self.total_bar.setRange(0, 1000)
        paused = self.sink.is_paused
        if self._last_tick is not None and not paused:
            self._elapsed += now - self._last_tick
        self._last_tick = now
        done, total = state.bytes_done, state.total_bytes
        share = min(1.0, done / total) if total > 0 else 0.0
        self.total_bar.setValue(int(share * 1000))
        size, current = state.current_size, state.current_done
        self.current_bar.setValue(int(min(1.0, current / size) * 1000) if size > 0 else 0)
        self._set_elided(self.current_label, tr("Elemento actual:  {current_name}", current_name=state.current_name), Qt.TextElideMode.ElideRight)
        self.current_size.setText(f"{format_size(current)} / {format_size(size)}")
        self.items_label.setText(tr("{items_done} / {total_items} elementos:", items_done=state.items_done, total_items=state.total_items))
        self.elapsed_label.setText(tr("{value} transcurrido", value=format_clock(self._elapsed)))
        title = tr("En pausa") if paused else self.verb
        self.setWindowTitle(f"({int(share * 100)}%) {title} - {APP_NAME}")
        self.skip_button.setEnabled(size > 0 and current < size and not state.skip_requested and self.cancel_button.isEnabled())
        if paused:
            self._history.clear()  # la pausa no cuenta como velocidad baja
            self.remaining_label.setText(tr("en pausa"))
            return
        speed = self._speed(now, done)
        average = done / self._elapsed if self._elapsed > 0 else 0.0
        rate = speed or average
        self.remaining_label.setText(tr("{value} restante", value=format_clock((total - done) / rate)) if rate > 0 else tr("calculando…"))
        if len(self._history) > 1:  # con una sola medida aún no hay velocidad: al empezar o tras una pausa, no es un cero
            self.graph.add_sample(speed, average)

    def skip(self) -> None:
        self.sink.skip_current()
        self.skip_button.setEnabled(False)

    def toggle_pause(self) -> None:
        if self.sink.is_paused:
            self.sink.resume()
            self.pause_button.setText(tr("Pausar"))
        else:
            self.sink.pause()
            self.pause_button.setText(tr("Reanudar"))
        self.tick()

    def cancel(self) -> None:
        self.cancel_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.skip_button.setEnabled(False)
        self.cancel_button.setText(tr("Cancelando…"))
        self.cancelRequested.emit()

    def job_finished(self) -> None:
        """El trabajo terminó, bien o mal: la ventana ya no pinta nada."""
        self._finished = True
        self._tick_timer.stop()
        self._show_timer.stop()
        self.hide()
        self.deleteLater()

    def reject(self) -> None:
        """Esc no hace nada: cancelar una copia sin querer sale caro."""

    def closeEvent(self, event) -> None:
        if not self._finished:  # como en cualquier ventana de copia, cerrarla es cancelar
            event.ignore()
            if self.cancel_button.isEnabled():
                self.cancel()
