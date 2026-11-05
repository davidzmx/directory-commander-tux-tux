"""Elegir qué grupos de ajustes se exportan o se importan, y qué se hace con las contraseñas SSH/SFTP."""
from __future__ import annotations

import html
import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ..core import settings_io
from ..core.i18n import tr

NO_PASSWORDS, ENCRYPTED, PLAIN = "none", "encrypted", "plain"
MIN_KEY_LENGTH = 8
_SYSTEMS = {"linux": "Linux", "windows": "Windows", "macos": "macOS"}


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


def _indented(widget: QWidget, depth: int = 1) -> QWidget:
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(24 * depth, 0, 0, 0)
    row.addWidget(widget, 1)
    return holder


class SettingsTransferDialog(QDialog):
    """Con `bundle`, para importar lo que trae ese archivo; sin él, para exportar."""

    def __init__(self, parent=None, bundle: settings_io.Bundle | None = None) -> None:
        super().__init__(parent)
        self.bundle = bundle
        exporting = bundle is None
        self.setWindowTitle(tr("Exportar configuración") if exporting else tr("Importar configuración"))
        layout = QVBoxLayout(self)
        intro = QLabel(tr("Marca lo que quieres llevar a otro equipo. Cada grupo se puede exportar e importar por separado.") if exporting
                       else tr("Marca lo que quieres importar. Lo marcado sustituye a lo que tienes; lo demás no cambia."))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if not exporting:
            for text in self._origin_notes():
                note = QLabel(text)
                note.setWordWrap(True)
                layout.addWidget(note)
        content = QWidget()
        rows = QVBoxLayout(content)
        self.checks: dict[str, QCheckBox] = {}
        present = [group.key for group in settings_io.GROUPS] if exporting else bundle.groups()
        for group in settings_io.GROUPS:
            if group.key not in present:
                continue
            check = QCheckBox(tr(group.label))
            check.setChecked(True)
            self.checks[group.key] = check
            rows.addWidget(check)
            rows.addWidget(_indented(_hint(tr(group.contents))))
            if group.key == settings_io.SSH:
                self._add_ssh_options(rows, exporting)
        rows.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # los textos se parten en líneas
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        if exporting:
            items = "".join(f"<li><b>{html.escape(tr(what))}</b>: {html.escape(tr(why))}</li>" for what, why in settings_io.NOT_EXPORTED)
            missing = QLabel(f"{html.escape(tr('Lo que no se exporta nunca:'))}<ul style='margin-top:2px'>{items}</ul>")
            missing.setWordWrap(True)
            layout.addWidget(missing)
        else:
            layout.addWidget(_hint(tr("Los botones de las barras pueden ejecutar programas: importa solo archivos de confianza.")))
        self.notice = QLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)
        every, none = QPushButton(tr("Marcar todo")), QPushButton(tr("Desmarcar todo"))
        every.clicked.connect(lambda: self._check_all(True))
        none.clicked.connect(lambda: self._check_all(False))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Exportar…") if exporting else tr("Importar"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancelar"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(every)
        bottom.addWidget(none)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)
        for check in self.checks.values():
            check.toggled.connect(self._update)
        self._update()
        self.resize(640, 700)

    def _origin_notes(self) -> list[str]:
        bundle = self.bundle
        home = os.path.expanduser("~")
        notes = []
        try:
            exported = datetime.fromisoformat(bundle.exported)
        except ValueError:
            pass
        else:
            notes.append(tr("Exportado el {date} a las {time}.", date=f"{exported:%d/%m/%Y}", time=f"{exported:%H:%M}"))
        if bundle.other_system:
            notes.append(tr("Este archivo se exportó en {system}. Las rutas de la carpeta personal de allí se cambian por la tuya.",
                            system=_SYSTEMS[bundle.platform]))
        elif bundle.home and bundle.home != home:
            notes.append(tr("Las rutas de {old} se cambian por las de {new}.", old=bundle.home, new=home))
        return notes

    def _add_ssh_options(self, rows: QVBoxLayout, exporting: bool) -> None:
        self.hosts = QCheckBox(tr("Las claves de los servidores (known_hosts)"))
        self.hosts.setChecked(True)
        hosts_hint = _hint(tr("Así no se vuelve a preguntar si confías en ellos."))
        self.passwords = QComboBox()
        self.key = QLineEdit()
        self.key_again = QLineEdit()
        self.import_passwords = QCheckBox()
        self.password_warning = _hint(tr("⚠ Viajarán en texto plano: cualquiera que tenga el archivo podrá leerlas. "
                                         "Guárdalo en un sitio seguro y bórralo cuando lo hayas importado."))
        if exporting:
            for value, label in ((NO_PASSWORDS, tr("No incluirlas: se piden al conectar por primera vez")), (ENCRYPTED, tr("Cifradas con una clave")),
                                 (PLAIN, tr("Tal cual, en texto plano"))):
                self.passwords.addItem(label, value)
            for field in (self.key, self.key_again):
                field.setEchoMode(QLineEdit.EchoMode.Password)
            self.key.setPlaceholderText(tr("Al menos {count} caracteres", count=MIN_KEY_LENGTH))
            self.key_holder = QWidget()
            form = QFormLayout(self.key_holder)
            form.setContentsMargins(0, 0, 0, 0)
            form.addRow(tr("Clave:"), self.key)
            form.addRow(tr("Repítela:"), self.key_again)
            form.addRow("", _hint(tr("Se pedirá al importar. Sin ella no se pueden leer las contraseñas.")))
            choice = QWidget()
            line = QHBoxLayout(choice)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(QLabel(tr("Contraseñas:")))
            line.addWidget(self.passwords, 1)
            rows.addWidget(_indented(self.hosts))
            rows.addWidget(_indented(hosts_hint, 2))
            rows.addWidget(_indented(choice))
            rows.addWidget(_indented(self.key_holder, 2))
            rows.addWidget(_indented(self.password_warning, 2))
            self.passwords.currentIndexChanged.connect(self._update)
            return
        bundle = self.bundle
        if bundle.known_hosts:
            rows.addWidget(_indented(self.hosts))
            rows.addWidget(_indented(hosts_hint, 2))
        if bundle.passwords:
            self.import_passwords.setText(tr("Las contraseñas, cifradas: se pedirá la clave") if bundle.encrypted
                                          else tr("Las contraseñas, que vienen en texto plano"))
            self.import_passwords.setChecked(True)
            rows.addWidget(_indented(self.import_passwords))

    def _check_all(self, checked: bool) -> None:
        for check in self.checks.values():
            check.setChecked(checked)

    def _update(self) -> None:
        ssh = self.checks.get(settings_io.SSH)
        on = ssh is not None and ssh.isChecked()
        if ssh is None:
            return
        self.hosts.setEnabled(on)
        self.passwords.setEnabled(on)
        self.import_passwords.setEnabled(on)
        if self.bundle is None:
            self.key_holder.setVisible(on and self.password_mode() == ENCRYPTED)
            self.password_warning.setVisible(on and self.password_mode() == PLAIN)

    # ---------------------------------------------------------------- lo elegido
    def groups(self) -> list[str]:
        return [key for key, check in self.checks.items() if check.isChecked()]

    def _ssh(self) -> bool:
        return settings_io.SSH in self.groups()

    def password_mode(self) -> str:
        return self.passwords.currentData() if self.bundle is None and settings_io.SSH in self.checks and self._ssh() else NO_PASSWORDS

    def password_key(self) -> str:
        return self.key.text() if self.password_mode() == ENCRYPTED else ""

    def known_hosts(self) -> bool:
        return settings_io.SSH in self.checks and self._ssh() and self.hosts.isChecked() and (self.bundle is None or bool(self.bundle.known_hosts))

    def passwords_wanted(self) -> bool:
        """Al importar: si se importan las contraseñas que trae el archivo."""
        return self.bundle is not None and bool(self.bundle.passwords) and self._ssh() and self.import_passwords.isChecked()

    def _accept(self) -> None:
        if not self.groups():
            problem = tr("Marca al menos un grupo")
        elif self.password_mode() == ENCRYPTED and len(self.key.text()) < MIN_KEY_LENGTH:
            problem = tr("La clave tiene que tener al menos {count} caracteres", count=MIN_KEY_LENGTH)
        elif self.password_mode() == ENCRYPTED and self.key.text() != self.key_again.text():
            problem = tr("Las dos claves no coinciden")
        else:
            self.accept()
            return
        self.notice.setText(problem)
        self.notice.show()
