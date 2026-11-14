"""Páginas de las preferencias para git y para ser el administrador de archivos del sistema."""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from ..core import default_manager, file_compare, git_tools
from ..core.config import is_portable
from ..core.default_manager import EXCEPT, FILESYSTEM, OFF
from ..core.i18n import tr
from . import theme


class GitPage(QWidget):
    barRequested = Signal()  # se pidió ir a la página de la barra de git

    def __init__(self, config) -> None:
        super().__init__()
        self.enabled = QCheckBox(tr("Mostrar git en las terminales cuando su carpeta sea un repositorio"))
        self.enabled.setChecked(bool(config.get("git_enabled", True)))
        self.show_branch = QCheckBox(tr("Mostrar la rama activa en la barra de las terminales"))
        self.show_branch.setChecked(bool(config.get("git_show_branch", True)))
        self.show_user = QCheckBox(tr("Mostrar el usuario de git en la barra de las terminales"))
        self.show_user.setChecked(bool(config.get("git_show_user", True)))
        self.pane_branch = QCheckBox(tr("Mostrar la rama activa en la barra de cada panel, si su carpeta es un repositorio"))
        self.pane_branch.setChecked(bool(config.get("git_pane_branch", True)))
        self.marks = QCheckBox(tr("Marcar en la lista de archivos su estado en el repositorio"))
        self.marks.setToolTip(tr("M modificado, A añadido, U sin seguimiento, R renombrado, C en conflicto. Una carpeta con cambios dentro "
                                 "lleva un punto del color del más importante; lo que git ignora se ve más tenue. Al pasar el ratón por una marca se dice qué significa"))
        self.marks.setChecked(config.get("git_marks") is not False)
        self.run_commands = QCheckBox(tr("Ejecutar la orden al aceptar. Si no, solo se escribe en la terminal para revisarla"))
        self.run_commands.setChecked(bool(config.get("git_run_commands", True)))
        self.program = QLineEdit(config.get("git_program", ""))
        self.program.setPlaceholderText(tr("Vacío: se busca en el PATH"))
        browse = QPushButton(tr("Elegir…"))
        browse.clicked.connect(self._choose)
        self.found = QLabel()
        self.found.setWordWrap(True)
        self.program.textChanged.connect(lambda _text: self._show_found())
        row = QHBoxLayout()
        row.addWidget(self.program, 1)
        row.addWidget(browse)
        form = QFormLayout()
        form.addRow(tr("Programa git:"), row)
        form.addRow("", self.found)
        link = f'<a href="bar" style="color: {theme.current().dir_text}">{tr("Barra de git de las terminales")}</a>'
        self.bar_link = QLabel(tr("Qué botones se ven, en qué orden y qué orden ejecuta cada uno se elige en {link}.", link=link))
        self.bar_link.setWordWrap(True)
        self.bar_link.setTextFormat(Qt.TextFormat.RichText)
        self.bar_link.linkActivated.connect(lambda _link: self.barRequested.emit())
        layout = QVBoxLayout(self)
        layout.addWidget(self.enabled)
        layout.addWidget(self.show_branch)
        layout.addWidget(self.show_user)
        layout.addWidget(self.pane_branch)
        layout.addWidget(self.marks)
        layout.addWidget(self.run_commands)
        layout.addLayout(form)
        layout.addWidget(self.bar_link)
        layout.addStretch(1)
        self._show_found()

    def _choose(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, tr("Programa git"))
        if path:
            self.program.setText(path)

    def _show_found(self) -> None:
        program = git_tools.git_program(self.program.text().strip())
        self.found.setText(tr("Se usará: {program}", program=program) if program else tr("No se ha encontrado git. Sin él, las terminales no muestran nada de esto."))

    def apply_to(self, config) -> None:
        config["git_enabled"] = self.enabled.isChecked()
        config["git_show_branch"] = self.show_branch.isChecked()
        config["git_show_user"] = self.show_user.isChecked()
        config["git_pane_branch"] = self.pane_branch.isChecked()
        config["git_marks"] = self.marks.isChecked()
        config["git_run_commands"] = self.run_commands.isChecked()
        config["git_program"] = self.program.text().strip()


class ComparePage(QWidget):
    """Con qué se abren los archivos que no son idénticos. Lista solo los programas que hay instalados."""

    def __init__(self, config) -> None:
        super().__init__()
        self.tool = QComboBox()
        self.tool.addItem(tr("Comparación interna"), "")
        tools = file_compare.detect_tools()
        for tool in tools:
            self.tool.addItem(tool.label, tool.key)
        self.tool.addItem(tr("Otro programa…"), file_compare.CUSTOM)
        self.tool.setCurrentIndex(max(0, self.tool.findData(config.get("compare_tool", ""))))
        self.command = QLineEdit(config.get("compare_command", ""))
        self.command.setPlaceholderText(tr("Programa y sus argumentos. {a} y {b} son los dos archivos; sin ellos, van al final"))
        self.browse = QPushButton(tr("Elegir…"))
        self.browse.clicked.connect(self._choose)
        self.images = QCheckBox(tr("Usarlo también para las imágenes. Si no, se comparan aquí, lado a lado"))
        self.images.setChecked(bool(config.get("compare_external_images", False)))
        self.found = QLabel(tr("Programas encontrados en este equipo: {names}", names=", ".join(tool.label for tool in tools)) if tools else tr("No se ha encontrado ningún comparador instalado."))
        self.found.setWordWrap(True)
        hint = QLabel(tr("«Comparar archivos» se activa con dos archivos seleccionados, en un panel o entre dos. Si son idénticos se dice sin abrir ningún programa. "
                         "La comparación interna usa diff para el texto y muestra las imágenes lado a lado con sus diferencias."))
        hint.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.command, 1)
        row.addWidget(self.browse)
        form = QFormLayout()
        form.addRow(tr("Comparar con:"), self.tool)
        form.addRow("", row)
        form.addRow("", self.images)
        form.addRow("", self.found)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addStretch(1)
        self.tool.currentIndexChanged.connect(lambda _row: self._show_choice())
        self._show_choice()

    def _show_choice(self) -> None:
        custom = self.tool.currentData() == file_compare.CUSTOM
        self.command.setVisible(custom)
        self.browse.setVisible(custom)
        self.images.setEnabled(bool(self.tool.currentData()))

    def _choose(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, tr("Programa para comparar"))
        if path:
            path = os.path.normpath(path)
            self.command.setText(f'"{path}"' if " " in path else path)

    def apply_to(self, config) -> None:
        config["compare_tool"] = self.tool.currentData()
        config["compare_command"] = self.command.text().strip()
        config["compare_external_images"] = self.images.isChecked()


class FileManagerPage(QWidget):
    """Lo que hay en el sistema se lee al abrir, y solo se toca si aquí se cambia algo. Se aplica con Aceptar y se puede deshacer de la misma forma."""

    def __init__(self, config) -> None:
        super().__init__()
        self._changed = False
        self.new_tab = QCheckBox(tr("Abrir una pestaña nueva en vez de otra ventana"))
        self.new_tab.setChecked(bool(config.get("file_manager_new_tab", True)))
        layout = QVBoxLayout(self)
        self.portable = is_portable()
        if self.portable:
            # El sistema guardaría la ruta del programa, y una versión portable se puede mover o estar en una memoria que no esté puesta.
            self.portable_notice = QLabel(tr("La versión portable no puede ser el administrador de archivos del sistema, porque se puede mover de sitio. Para eso, usa la versión instalada."))
            self.portable_notice.setWordWrap(True)
            self.portable_notice.setObjectName("prefsInfo")
            layout.addWidget(self.portable_notice)
            layout.addStretch(1)
            return
        self.windows = sys.platform == "win32"
        self._build_windows(layout, config) if self.windows else self._build_linux(layout)
        layout.addWidget(self.new_tab)
        layout.addStretch(1)

    # ---------------------------------------------------------------- Windows
    def _build_windows(self, layout: QVBoxLayout, config) -> None:
        intro = QLabel(tr("Hace que las carpetas se abran en este programa en lugar del Explorador de archivos: al abrirlas desde el escritorio, desde otros programas o con el Explorador. "
                          "Solo cambia los ajustes de tu usuario, no hacen falta permisos de administrador y se puede deshacer eligiendo la primera opción. "
                          "La Papelera, el Panel de control y otras carpetas que no están en el disco siguen abriéndose en el Explorador, porque aquí no se pueden mostrar."))
        intro.setWordWrap(True)
        intro.setObjectName("prefsInfo")
        replaced, win_e = self._read_windows()
        mode = (EXCEPT if config.get("file_manager_exceptions") else FILESYSTEM) if replaced else OFF
        self.off = QRadioButton(tr("No reemplazar al Explorador de archivos"))
        self.filesystem = QRadioButton(tr("Reemplazar al Explorador de archivos para todas las carpetas"))
        self.except_some = QRadioButton(tr("Reemplazar al Explorador de archivos para todas las carpetas, excepto las siguientes:"))
        self.group = QButtonGroup(self)
        for button, key in ((self.off, OFF), (self.filesystem, FILESYSTEM), (self.except_some, EXCEPT)):
            self.group.addButton(button)
            button.setChecked(key == mode)
            button.toggled.connect(self._user_changed)
        self.exceptions = QListWidget()
        self.exceptions.addItems(config.get("file_manager_exceptions") or [])
        self.add_exception = QPushButton(tr("Añadir…"))
        self.remove_exception = QPushButton(tr("Eliminar"))
        self.add_exception.clicked.connect(self._add_exception)
        self.remove_exception.clicked.connect(self._remove_exception)
        self.win_e = QCheckBox(tr("Abrir también con Win+E"))
        self.win_e.setChecked(win_e)
        self.win_e.toggled.connect(self._user_changed)
        self.handler = QLabel()
        self.handler.setWordWrap(True)
        self.handler.setTextFormat(Qt.TextFormat.PlainText)
        self.show_handler()
        tools = QHBoxLayout()
        tools.addWidget(self.add_exception)
        tools.addWidget(self.remove_exception)
        tools.addStretch(1)
        for widget in (intro, self.off, self.filesystem, self.except_some):
            layout.addWidget(widget)
        layout.addLayout(tools)
        layout.addWidget(self.exceptions, 1)
        layout.addWidget(self.win_e)
        layout.addWidget(self.handler)
        self._user_changed()
        self._changed = False

    @staticmethod
    def _read_windows() -> tuple[bool, bool]:
        return default_manager.windows_state(default_manager.Registry())

    def show_handler(self) -> None:
        handler = default_manager.windows_folder_handler()
        self.handler.setText(tr("Ahora las carpetas se abren con: {handler}", handler=handler) if handler else "")

    def _user_changed(self) -> None:
        self._changed = True
        if self.windows:
            excepting = self.except_some.isChecked()
            self.exceptions.setEnabled(excepting)
            self.add_exception.setEnabled(excepting)
            self.remove_exception.setEnabled(excepting)
            self.win_e.setEnabled(not self.off.isChecked())

    def _add_exception(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Carpeta que seguirá abriendo el Explorador"))
        if folder:
            self.exceptions.addItem(folder.replace("/", "\\"))

    def _remove_exception(self) -> None:
        self.exceptions.takeItem(self.exceptions.currentRow())

    def _exception_list(self) -> list[str]:
        return [self.exceptions.item(row).text() for row in range(self.exceptions.count())]

    def _apply_windows(self, config) -> None:
        exceptions = self._exception_list() if self.except_some.isChecked() else []
        config["file_manager_exceptions"] = exceptions
        if not self._changed:
            return
        if self.off.isChecked():
            default_manager.restore_system_default()
        else:
            default_manager.become_windows_default(self.win_e.isChecked())
        self._changed = False
        self.show_handler()

    # ---------------------------------------------------------------- Linux
    def _build_linux(self, layout: QVBoxLayout) -> None:
        intro = QLabel(tr("Hace que las carpetas se abran en este programa en lugar de Dolphin, Nautilus u otro. Se guarda cuál era, para poder volver a él."))
        intro.setWordWrap(True)
        intro.setObjectName("prefsInfo")
        available = default_manager.xdg_available()
        try:
            current = default_manager.linux_default() if available else ""
        except (OSError, subprocess.SubprocessError):
            current = ""
        self.default_box = QCheckBox(tr("Usar como administrador de archivos predeterminado"))
        self.default_box.setChecked(current == default_manager.DESKTOP_FILE)
        self.default_box.setEnabled(available)
        self.default_box.toggled.connect(self._user_changed)
        self.handler = QLabel(tr("Ahora las carpetas se abren con: {handler}", handler=current) if current else (tr("No se ha encontrado xdg-mime.") if not available else ""))
        self.handler.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(intro)
        layout.addWidget(self.default_box)
        layout.addWidget(self.handler)

    def _apply_linux(self, config) -> None:
        if not self._changed:
            return
        if self.default_box.isChecked():
            previous = default_manager.become_linux_default(default_manager.launch_arguments())
            if previous:
                config["file_manager_previous"] = previous
        else:
            default_manager.restore_system_default(config.get("file_manager_previous", ""))
        self._changed = False

    def apply_to(self, config) -> None:
        if self.portable:
            return
        config["file_manager_new_tab"] = self.new_tab.isChecked()
        try:
            self._apply_windows(config) if self.windows else self._apply_linux(config)
        except (OSError, subprocess.SubprocessError) as exc:
            QMessageBox.warning(self, tr("Administrador de archivos"), tr("No se pudo cambiar: {exc}", exc=exc))
