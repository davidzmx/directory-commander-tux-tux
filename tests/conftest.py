import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402 - la plataforma de Qt debe fijarse antes de cargarlo

from directory_commander_tux_tux.core import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def source_language_again():
    """`app.main` fija el idioma del sistema: sin esto, en un equipo en inglés las pruebas siguientes verían los textos traducidos."""
    yield
    i18n.set_language(i18n.SOURCE_CODE)


@pytest.fixture(autouse=True)
def no_real_devices(monkeypatch):
    """Los discos de este equipo no deben cambiar lo que ven las pruebas, ni ninguna prueba montarlos: las que los usan dan los suyos."""
    from directory_commander_tux_tux.core import devices

    monkeypatch.setattr(devices, "list_devices", lambda: [])
    monkeypatch.setattr(devices, "_run", lambda argv, timeout: pytest.fail(f"Una prueba intentó ejecutar {argv[0]}"))
    from directory_commander_tux_tux.ui import udisks

    monkeypatch.setattr(udisks.UDisks, "bus", lambda self: None)  # sin el bus del sistema: se usa `devices`, que cada prueba sustituye


@pytest.fixture(autouse=True)
def no_real_programs(monkeypatch):
    """Abrir un archivo pasa por QDesktopServices, que cada prueba sustituye por lo que necesita: nunca por xdg-open, que abriría el programa de verdad."""
    from directory_commander_tux_tux.ui import open_external

    monkeypatch.setattr(open_external, "_use_xdg_open", lambda: False)


@pytest.fixture
def qapp_for_gc():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def destroy_windows():
    """Una ventana viva ralentiza las demás: cada hoja de estilos nueva vuelve a pulirlas todas."""
    yield
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
