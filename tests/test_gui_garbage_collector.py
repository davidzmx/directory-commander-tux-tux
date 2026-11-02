"""El recolector de basura solo debe correr en el hilo de la interfaz: en otro, destruir un widget hace caer el programa."""
import gc
import threading
import time

from PySide6.QtWidgets import QApplication, QWidget

from directory_commander_tux_tux.ui.background import _GuiGarbageCollector, collect_garbage_in_gui_thread


class Cycle:
    """Basura que solo el recolector puede liberar, y que apunta en qué hilo lo hizo."""
    seen: list = []

    def __init__(self) -> None:
        self.itself = self

    def __del__(self) -> None:
        Cycle.seen.append(threading.current_thread())


KEPT: list = []


def allocate_a_lot() -> None:
    """Sin liberarlo: lo que cuenta para el recolector es lo reservado y aún en uso."""
    KEPT.extend([index] for index in range(200000))


def wait_until(condition, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_garbage_is_collected_in_the_gui_thread_even_if_a_worker_allocates(qapp_for_gc):
    collect_garbage_in_gui_thread()
    assert not gc.isenabled()
    Cycle.seen = []
    Cycle()
    worker = threading.Thread(target=allocate_a_lot)
    worker.start()
    worker.join()
    assert Cycle.seen == []  # reservar tanta memoria no ha hecho que se recoja en el otro hilo
    assert wait_until(lambda: Cycle.seen)
    assert Cycle.seen == [threading.current_thread()]
    KEPT.clear()


def test_widgets_in_a_cycle_die_in_the_gui_thread(qapp_for_gc):
    collect_garbage_in_gui_thread()
    destroyed = []
    widget = QWidget()
    widget.destroyed.connect(lambda: destroyed.append(threading.current_thread()))
    widget.itself = widget
    del widget
    worker = threading.Thread(target=allocate_a_lot)
    worker.start()
    worker.join()
    assert destroyed == []
    assert wait_until(lambda: destroyed)
    assert destroyed == [threading.current_thread()]
    KEPT.clear()


def test_only_one_collector_with_several_calls(qapp_for_gc):
    collect_garbage_in_gui_thread()
    collect_garbage_in_gui_thread()
    assert len(qapp_for_gc.findChildren(_GuiGarbageCollector)) == 1


def test_window_turns_it_on(qapp_for_gc, tmp_path, monkeypatch):
    from directory_commander_tux_tux.core.config import Config
    from directory_commander_tux_tux.ui.main_window import MainWindow
    gc.enable()
    for collector in qapp_for_gc.findChildren(_GuiGarbageCollector):
        collector.setParent(None)
        collector.deleteLater()
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    window = MainWindow(Config(), [str(tmp_path)])
    assert not gc.isenabled() and len(qapp_for_gc.findChildren(_GuiGarbageCollector)) == 1
    window.close()
