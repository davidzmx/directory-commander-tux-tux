"""Página de las preferencias para elegir qué botones muestra cada barra y en qué orden."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..core import bar_buttons, git_tools
from ..core.bar_buttons import ALWAYS_SHOWN, SEPARATOR, SPACER
from ..core.i18n import N_, tr
from .context_menu_pages import SEPARATOR_TEXT

KEY_ROLE = Qt.ItemDataRole.UserRole
_COMMAND_HINTS = {git_tools.ADD: N_("Abre una ventana para elegir los archivos, que se añaden detrás de esta orden."),
                  git_tools.UNSTAGE: N_("Abre una ventana para elegir los archivos, que se añaden detrás de esta orden."),
                  git_tools.COMMIT: N_("Abre una ventana para escribir el mensaje y elegir los archivos, que se añaden detrás de esta orden.")}


class GitCommandEditor(QWidget):
    """Lo que ejecuta cada botón de la barra de git. Las órdenes que trae el programa solo cambian de orden; las añadidas, también de nombre."""

    def __init__(self, config, page: "BarButtonsPage") -> None:
        super().__init__()
        self.page = page
        self.commands = git_tools.clean_commands(config.get("git_commands"))
        self.custom = git_tools.clean_custom(config.get("git_custom_commands"))
        self._key = ""
        self._loading = False
        self.name = QLineEdit()
        self.command = QLineEdit()
        self.command.setPlaceholderText(tr("Orden que se escribe en la terminal"))
        self.files = QCheckBox(tr("Elegir archivos antes de ejecutarla: se añaden detrás de la orden"))
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.add_button = QPushButton(tr("Añadir una orden"))
        self.remove_button = QPushButton(tr("Eliminar esta orden"))
        self.restore_button = QPushButton(tr("Volver a la orden original"))
        self.add_button.clicked.connect(lambda _=False: self.add())
        self.remove_button.clicked.connect(lambda _=False: self.remove())
        self.restore_button.clicked.connect(lambda _=False: self.restore())
        self.name.textEdited.connect(lambda _text: self._edited())
        self.command.textEdited.connect(lambda _text: self._edited())
        self.files.toggled.connect(lambda _checked: self._edited())
        form = QFormLayout()
        form.addRow(tr("Nombre:"), self.name)
        form.addRow(tr("Orden:"), self.command)
        form.addRow("", self.files)
        form.addRow("", self.hint)
        row = QHBoxLayout()
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addWidget(self.restore_button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addLayout(row)

    def _custom_item(self, key: str) -> dict | None:
        return next((item for item in self.custom if item["key"] == key), None)

    def show_key(self, key: str) -> None:
        self._key = key
        custom = self._custom_item(key)
        self._loading = True
        self.name.setText(custom["label"] if custom else self.page.label_of(key))
        self.command.setText(custom["command"] if custom else self.commands.get(key, git_tools.DEFAULT_COMMANDS.get(key, "")))
        self.files.setChecked(bool(custom and custom["files"]))
        self._loading = False
        self.name.setEnabled(custom is not None)
        self.command.setEnabled(bool(key))
        self.files.setVisible(custom is not None)
        self.remove_button.setEnabled(custom is not None)
        self.hint.setText(tr(_COMMAND_HINTS[key]) if key in _COMMAND_HINTS else "")
        self.hint.setVisible(key in _COMMAND_HINTS)
        self._update_restore()

    def _update_restore(self) -> None:
        self.restore_button.setEnabled(self._key in git_tools.DEFAULT_COMMANDS and self.command.text().strip() != git_tools.DEFAULT_COMMANDS[self._key])

    def _edited(self) -> None:
        if self._loading or not self._key:
            return
        custom = self._custom_item(self._key)
        command = self.command.text().strip()
        if custom:
            custom.update(label=self.name.text().strip(), command=command, files=self.files.isChecked())
            self.page.relabel_git(self._key, custom["label"] or command)
        else:
            self.commands[self._key] = command
        self._update_restore()

    def add(self) -> None:
        key = git_tools.new_custom_key(self.custom)
        self.custom.append({"key": key, "label": tr("Nueva orden"), "command": "git ", "files": False})
        self.page.add_git_button(key, tr("Nueva orden"))
        self.command.setFocus()

    def remove(self) -> None:
        if self._custom_item(self._key):
            key, self._key = self._key, ""
            self.custom = [item for item in self.custom if item["key"] != key]
            self.page.remove_git_button(key)

    def restore(self) -> None:
        self.commands.pop(self._key, None)
        self.show_key(self._key)

    def apply_to(self, config) -> None:
        config["git_commands"] = git_tools.clean_commands(self.commands)
        config["git_custom_commands"] = git_tools.clean_custom(self.custom)


class BarButtonsPage(QWidget):
    def __init__(self, config) -> None:
        super().__init__()
        self.lists: dict[str, QListWidget] = {}
        self._defaults: dict[str, list[str]] = {}
        self._labels: dict[str, dict[str, str]] = {}
        stored = bar_buttons.normalize(config.get("bar_buttons"))
        self.bar_list = QListWidget()
        self.bar_list.setMaximumWidth(250)
        self.stack = QStackedWidget()
        for bar, title, buttons in bar_buttons.bars(config):
            if bar == bar_buttons.STYLES:
                continue  # se eligen y se ordenan en la página de los estilos, que es donde se crean
            self.bar_list.addItem(title)
            buttons_list = QListWidget()
            buttons_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
            buttons_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.lists[bar] = buttons_list
            self._defaults[bar] = [key for key, _label in buttons]
            self._labels[bar] = {**dict(buttons), SEPARATOR: SEPARATOR_TEXT, SPACER: tr("Espacio flexible")}
            for key in bar_buttons.ordered(config, bar, self._defaults[bar]):
                self._add_item(bar, key, key not in stored.get(bar, ()))
            self.stack.addWidget(buttons_list)
        hint = QLabel(tr("Desmarca los botones que no quieras ver y cambia su orden con ▲ y ▼ o arrastrándolos. "
                         "Lo que quites de una barra sigue disponible en los menús y con su atajo de teclado."))
        hint.setWordWrap(True)
        self.add_line_button = self._button(tr("+ Línea"), tr("Añadir una línea separadora"), self.add_line)
        self.remove_line_button = self._button(tr("− Línea"), tr("Quitar la línea separadora seleccionada"), self.remove_line)
        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(3)
        buttons_row.addWidget(self._button("▲", tr("Subir"), lambda: self.move_current(-1)))
        buttons_row.addWidget(self._button("▼", tr("Bajar"), lambda: self.move_current(1)))
        buttons_row.addWidget(self.add_line_button)
        buttons_row.addWidget(self.remove_line_button)
        buttons_row.addWidget(self._button(tr("Mostrar todos"), tr("Marca todos los botones de esta barra"), self.show_all))
        buttons_row.addWidget(self._button(tr("Restablecer"), tr("Volver al orden original de esta barra"), self.reset_order))
        buttons_row.addStretch(1)
        self.git_editor = GitCommandEditor(config, self)
        git_list = self.lists[bar_buttons.GIT]
        git_list.currentItemChanged.connect(lambda item, _before: self.git_editor.show_key(item.data(KEY_ROLE) if item is not None else ""))
        git_list.setCurrentRow(0)
        column = QVBoxLayout()
        column.addWidget(self.stack, 1)
        column.addLayout(buttons_row)
        column.addWidget(self.git_editor)
        row = QHBoxLayout()
        row.addWidget(self.bar_list)
        row.addLayout(column, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(row, 1)
        self.bar_list.currentRowChanged.connect(self._bar_changed)
        self.bar_list.setCurrentRow(0)

    @staticmethod
    def _button(label: str, tip: str, slot) -> QPushButton:
        button = QPushButton(label)
        button.setToolTip(tip)
        button.clicked.connect(lambda _checked=False: slot())
        return button

    def _add_item(self, bar: str, key: str, shown: bool = True, row: int | None = None) -> None:
        item = QListWidgetItem(self._labels[bar].get(key, key))
        item.setData(KEY_ROLE, key)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        if key not in ALWAYS_SHOWN:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setCheckState(Qt.CheckState.Checked if shown else Qt.CheckState.Unchecked)
        item.setFlags(flags)
        if row is None:
            self.lists[bar].addItem(item)
        else:
            self.lists[bar].insertItem(row, item)

    @property
    def current_bar(self) -> str:
        return list(self.lists)[self.bar_list.currentRow()]

    def _bar_changed(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        lines = bar_buttons.can_have_lines(self.current_bar)
        self.add_line_button.setVisible(lines)
        self.remove_line_button.setVisible(lines)
        self.git_editor.setVisible(self.current_bar == bar_buttons.GIT)

    def label_of(self, key: str) -> str:
        return self._labels[bar_buttons.GIT].get(key, key)

    def relabel_git(self, key: str, label: str) -> None:
        self._labels[bar_buttons.GIT][key] = label
        for item in self._items(bar_buttons.GIT):
            if item.data(KEY_ROLE) == key:
                item.setText(label)

    def add_git_button(self, key: str, label: str) -> None:
        self._labels[bar_buttons.GIT][key] = label
        self._defaults[bar_buttons.GIT].append(key)
        self._add_item(bar_buttons.GIT, key)
        git_list = self.lists[bar_buttons.GIT]
        git_list.setCurrentRow(git_list.count() - 1)

    def remove_git_button(self, key: str) -> None:
        self._defaults[bar_buttons.GIT].remove(key)
        git_list = self.lists[bar_buttons.GIT]
        for item in self._items(bar_buttons.GIT):
            if item.data(KEY_ROLE) == key:
                git_list.takeItem(git_list.row(item))

    def apply_git_to(self, config) -> None:
        self.git_editor.apply_to(config)

    def show_bar(self, bar: str) -> None:
        if bar in self.lists:
            self.bar_list.setCurrentRow(list(self.lists).index(bar))

    def move_current(self, delta: int) -> None:
        buttons_list = self.stack.currentWidget()
        row = buttons_list.currentRow()
        target = row + delta
        if row >= 0 and 0 <= target < buttons_list.count():
            buttons_list.insertItem(target, buttons_list.takeItem(row))
            buttons_list.setCurrentRow(target)

    def add_line(self) -> None:
        buttons_list = self.stack.currentWidget()
        row = buttons_list.currentRow()
        self._add_item(self.current_bar, SEPARATOR, row=row + 1 if row >= 0 else buttons_list.count())

    def remove_line(self) -> None:
        buttons_list = self.stack.currentWidget()
        item = buttons_list.currentItem()
        if item is not None and item.data(KEY_ROLE) in (SEPARATOR, SPACER):
            buttons_list.takeItem(buttons_list.row(item))

    def show_all(self) -> None:
        for item in self._items(self.current_bar):
            if item.data(KEY_ROLE) not in ALWAYS_SHOWN:
                item.setCheckState(Qt.CheckState.Checked)

    def reset_order(self) -> None:
        """Devuelve el orden y las líneas de serie. Lo marcado o desmarcado se queda como estaba."""
        bar = self.current_bar
        removed = {item.data(KEY_ROLE) for item in self._items(bar)
                   if item.data(KEY_ROLE) not in ALWAYS_SHOWN and item.checkState() != Qt.CheckState.Checked}
        self.lists[bar].clear()
        for key in self._defaults[bar]:
            self._add_item(bar, key, key not in removed)

    def _items(self, bar: str) -> list[QListWidgetItem]:
        buttons_list = self.lists[bar]
        return [buttons_list.item(row) for row in range(buttons_list.count())]

    def values(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for bar in self.lists:
            removed = [item.data(KEY_ROLE) for item in self._items(bar)
                       if item.data(KEY_ROLE) not in ALWAYS_SHOWN and item.checkState() != Qt.CheckState.Checked]
            if removed:
                result[bar] = removed
        return result

    def order_values(self) -> dict[str, list[str]]:
        """Solo las barras con un orden propio: las demás seguirán lo que traigan de serie versiones futuras."""
        result: dict[str, list[str]] = {}
        for bar in self.lists:
            order = [item.data(KEY_ROLE) for item in self._items(bar)]
            if order != self._defaults[bar]:
                result[bar] = order
        return result
