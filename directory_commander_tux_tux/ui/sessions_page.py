"""Página de las preferencias para administrar las sesiones guardadas."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QInputDialog, QLabel, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ..core import sessions
from ..core.i18n import tr
from .style_preview import StylePreview


class SessionsPage(QWidget):
    """Los cambios se aplican con Aceptar, como en el resto de las preferencias."""

    def __init__(self, config) -> None:
        super().__init__()
        self.known_styles = config.get("styles") if isinstance(config.get("styles"), list) else []
        self.renames: list[tuple[str, str]] = []  # para que la ventana siga a la sesión abierta si le cambia el nombre
        self.items = sessions.clean(config.get("saved_sessions"))
        self.list = QListWidget()
        self.rename_button = QPushButton(tr("Renombrar…"))
        self.delete_button = QPushButton(tr("Eliminar"))
        self.rename_button.clicked.connect(self._ask_rename)
        self.delete_button.clicked.connect(self.delete_current)
        self.list.setMaximumWidth(320)
        self.list.currentRowChanged.connect(lambda _row: self._update_buttons())
        self.preview = StylePreview(config)
        self.style_label = QLabel()
        self.list.itemDoubleClicked.connect(lambda _item: self._ask_rename())
        self.restore = QCheckBox(tr("Recordar la disposición y la sesión al cerrar el programa. Si no, empieza con el estilo Vertical doble panel"))
        self.restore.setChecked(bool(config.get("restore_session", True)))
        hint = QLabel(tr("Una sesión guarda los paneles, las pestañas y las terminales. Se cargan con la lista de arriba a la derecha de la ventana."))
        hint.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self.rename_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        side = QVBoxLayout()
        side.addWidget(self.preview, 1)
        side.addWidget(self.style_label)
        row = QHBoxLayout()
        row.addWidget(self.list, 1)
        row.addLayout(side, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(self.restore)
        layout.addWidget(hint)
        layout.addLayout(row, 1)
        layout.addLayout(buttons)
        self._fill()

    def _fill(self, select: str = "") -> None:
        self.list.clear()
        for item in self.items:
            panes, tabs = sessions.describe(item["data"])
            self.list.addItem(tr("{name}  ({panes} paneles, {tabs} pestañas)", name=item["name"], panes=panes, tabs=tabs))
        names = [item["name"] for item in self.items]
        self.list.setCurrentRow(names.index(select) if select in names else (0 if names else -1))
        self._update_buttons()

    def _update_buttons(self) -> None:
        chosen = self.list.currentRow() >= 0
        self.rename_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen)
        self._show_preview()

    def _show_preview(self) -> None:
        row = self.list.currentRow()
        self.preview.setVisible(0 <= row < len(self.items))
        self.style_label.setVisible(0 <= row < len(self.items))
        if 0 <= row < len(self.items):
            data = self.items[row]["data"]
            self.preview.set_session(sessions.layout_of(data, self.known_styles), sessions.tab_labels(data))
            self.style_label.setText(tr("Estilo: {style}", style=tr(data["style"])) if isinstance(data.get("style"), str) and data["style"] else "")

    def current_name(self) -> str:
        row = self.list.currentRow()
        return self.items[row]["name"] if 0 <= row < len(self.items) else ""

    def _ask_rename(self) -> None:
        old = self.current_name()
        if old:
            new, accepted = QInputDialog.getText(self, tr("Renombrar la sesión"), tr("Nombre de la sesión:"), text=old)
            if accepted:
                self.rename(old, new)

    def rename(self, old: str, new: str) -> bool:
        try:
            self.items = sessions.renamed(self.items, old, new)
        except ValueError:
            QMessageBox.warning(self, tr("Renombrar la sesión"), tr("Ese nombre está vacío o ya lo usa otra sesión."))
            return False
        self.renames.append((old, new.strip()))
        self._fill(new.strip())
        return True

    def delete_current(self) -> None:
        name = self.current_name()
        if name:
            self.items = sessions.removed(self.items, name)
            self._fill()

    def values(self) -> list[dict]:
        return self.items

    def followed_name(self, name: str) -> str:
        """Cómo se llama ahora esa sesión después de los cambios de nombre aplicados. Se pregunta una vez por cada vez que se aplican."""
        for old, new in self.renames:
            if old.casefold() == name.casefold():
                name = new
        self.renames = []
        return name
