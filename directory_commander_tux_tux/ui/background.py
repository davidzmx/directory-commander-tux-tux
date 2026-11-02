"""Trabajo en segundo plano. Los resultados siempre vuelven al hilo de la interfaz."""
from __future__ import annotations

import gc
import threading

from PySide6.QtCore import QCoreApplication, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot

from ..core.fsops import ProgressSink


_COLLECT_EVERY_MS = 500


class _GuiGarbageCollector(QObject):
    """El recolector de Python se dispara en el hilo que esté reservando memoria, y puede ser uno de trabajo. Si ahí encuentra widgets sin usar
    los destruye desde ese hilo, y Qt no lo admite: el programa se cae. Por eso se desactiva y se llama desde el hilo de la interfaz, con sus mismos límites."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        gc.disable()
        self._timer = QTimer(self, interval=_COLLECT_EVERY_MS)
        self._timer.timeout.connect(self._collect_if_needed)
        self._timer.start()

    @staticmethod
    def _collect_if_needed() -> None:
        counts, limits = gc.get_count(), gc.get_threshold()
        if counts[0] > limits[0]:
            gc.collect(2 if counts[2] > limits[2] else 1 if counts[1] > limits[1] else 0)


def collect_garbage_in_gui_thread() -> None:
    """Se llama desde el hilo de la interfaz, con la aplicación ya creada. Basta una vez, aunque haya varias ventanas."""
    app = QCoreApplication.instance()
    if app.findChild(_GuiGarbageCollector) is None:
        _GuiGarbageCollector(app)


class GuiAsker(QObject):
    """Deja que un hilo de trabajo pregunte a la interfaz y espere la respuesta. Se crea en el hilo de la interfaz."""

    _asked = Signal(object)

    def __init__(self, ask, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ask = ask
        self._asked.connect(self._answer, Qt.ConnectionType.QueuedConnection)

    def __call__(self, sink: ProgressSink, *args):
        request = {"args": args, "done": threading.Event(), "answer": None}
        self._asked.emit(request)
        while not request["done"].wait(0.05):
            sink.check()  # si se cancela o se cierra la ventana, el hilo no se queda esperando para siempre
        return request["answer"]

    @Slot(object)
    def _answer(self, request: dict) -> None:
        try:
            request["answer"] = self._ask(*request["args"])
        finally:
            request["done"].set()


class BgCall(QObject):
    """Ejecuta `fn(sink)` en otro hilo. Los callbacks corren en el hilo que creó el objeto,
    porque las señales están conectadas a métodos de este QObject."""

    _result = Signal(object)
    _failure = Signal(object)
    _progress = Signal(object)

    _active: set["BgCall"] = set()

    def __init__(self, fn, on_done=None, on_error=None, on_progress=None) -> None:
        super().__init__()
        self._fn, self._on_done, self._on_error, self._on_prog = fn, on_done, on_error, on_progress
        self.sink = ProgressSink(lambda done, total, name: self._progress.emit((done, total, name)))
        self._result.connect(self._handle_result)
        self._failure.connect(self._handle_failure)
        self._progress.connect(self._handle_progress)

    def cancel(self) -> None:
        self.sink.cancel()

    def run(self) -> None:
        try:
            value = self._fn(self.sink)
        except BaseException as exc:  # noqa: BLE001 - un hilo no debe tumbar la aplicación
            self._failure.emit(exc)
        else:
            self._result.emit(value)

    @Slot(object)
    def _handle_result(self, value) -> None:
        BgCall._active.discard(self)
        if self._on_done:
            self._on_done(value)

    @Slot(object)
    def _handle_failure(self, exc) -> None:
        BgCall._active.discard(self)
        if self._on_error:
            self._on_error(exc)

    @Slot(object)
    def _handle_progress(self, value) -> None:
        if self._on_prog:
            self._on_prog(value)


class _Runnable(QRunnable):
    def __init__(self, call: BgCall) -> None:
        super().__init__()
        self._call = call

    def run(self) -> None:
        self._call.run()


def run_bg(fn, on_done=None, on_error=None, on_progress=None, pool: QThreadPool | None = None) -> BgCall:
    call = BgCall(fn, on_done, on_error, on_progress)
    BgCall._active.add(call)
    (pool or QThreadPool.globalInstance()).start(_Runnable(call))
    return call
