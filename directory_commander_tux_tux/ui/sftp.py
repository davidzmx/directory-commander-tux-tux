"""Lo de los paneles remotos que se ve: el perfil de una conexión, su página de las preferencias, conectar preguntando lo que haga falta,
y abrir o previsualizar archivos de un servidor descargándolos a una carpeta temporal."""
from __future__ import annotations

import os
import shutil
import tempfile

import shiboken6

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core import remote
from ..core.i18n import N_, tr
from .background import run_bg

PROFILES_KEY = "ssh_profiles"  # sirven para los paneles remotos (SFTP) y para las terminales (SSH)
OPEN_KEY, PREVIEW_KEY, PREVIEW_MB_KEY, UPLOAD_KEY = "sftp_open_files", "sftp_preview", "sftp_preview_mb", "sftp_upload"
_AUTH_LABELS = (("password", N_("Contraseña")), ("key", N_("Clave privada")), ("agent", N_("Agente SSH o claves de ~/.ssh")))
_session_passwords: dict[str, str] = {}  # las escritas al conectar: duran hasta cerrar el programa


def profiles(config) -> list[remote.Profile]:
    return [remote.profile_from(item) for item in remote.clean_profiles(config.get(PROFILES_KEY))]


def open_files(config) -> bool:
    return config.get(OPEN_KEY, True) is not False


def uploads_without_asking(config) -> bool:
    return config.get(UPLOAD_KEY) == "auto"


class RemoteChanged(OSError):
    """El archivo ha cambiado en el servidor desde que se descargó: subir la copia borraría ese cambio."""


def previews(config) -> bool:
    return config.get(PREVIEW_KEY) is True


def preview_limit(config) -> int:
    value = config.get(PREVIEW_MB_KEY, remote.PREVIEW_DEFAULT_MB)
    return (value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 1024 else remote.PREVIEW_DEFAULT_MB) * 1024 * 1024


# ------------------------------------------------------------------ el perfil
class ProfileDialog(QDialog):
    """Los datos de una conexión. Con `quick`, para conectar ya, con la opción de guardarla como perfil."""

    def __init__(self, profile: remote.Profile | None = None, quick: bool = False, taken=(), parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Conexión nueva") if quick else tr("Perfil SSH/SFTP"))
        self._taken = {name.casefold() for name in taken}
        self.name = QLineEdit(profile.name if profile else "")
        self.host = QLineEdit(profile.host if profile else "")
        self.host.setPlaceholderText(tr("servidor.ejemplo.org o 192.168.1.10"))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(profile.port if profile else remote.DEFAULT_PORT)
        self.user = QLineEdit(profile.user if profile else "")
        self.auth = QComboBox()
        for value, label in _AUTH_LABELS:
            self.auth.addItem(tr(label), value)
        self.auth.setCurrentIndex(max(0, self.auth.findData(profile.auth if profile else "password")))
        self.key_file = QLineEdit(profile.key_file if profile else "")
        self.key_file.setPlaceholderText(tr("~/.ssh/id_ed25519"))
        browse = QPushButton(tr("Elegir…"))
        browse.clicked.connect(self._choose_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_file, 1)
        key_row.addWidget(browse)
        self.folder = QLineEdit(profile.folder if profile else "")
        self.folder.setPlaceholderText(tr("Vacío: la carpeta personal en el servidor"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(tr("Déjala vacía para no cambiar la guardada") if profile else tr("Si la dejas vacía, se pide al conectar"))
        self.save_password = QCheckBox(tr("Guardar la contraseña en el llavero del sistema"))
        self.save_password.setChecked(profile.save_password if profile else True)
        self.save_profile = QCheckBox(tr("Guardar como perfil"))
        self.save_profile.setChecked(True)
        self.save_profile.setVisible(quick)
        self.notice = QLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        form = QFormLayout()
        form.addRow(tr("Nombre:"), self.name)
        form.addRow(tr("Servidor:"), self.host)
        form.addRow(tr("Puerto:"), self.port)
        form.addRow(tr("Usuario:"), self.user)
        form.addRow(tr("Identificarse con:"), self.auth)
        form.addRow(tr("Clave privada:"), key_row)
        self.password_label = QLabel(tr("Contraseña:"))
        form.addRow(self.password_label, self.password)
        form.addRow(tr("Carpeta inicial:"), self.folder)
        form.addRow("", self.save_password)
        form.addRow("", self.save_profile)
        self._stored = profile  # para probar sin volver a escribir la contraseña guardada
        self.test_button = QPushButton(tr("Probar conexión"))
        self.test_button.setToolTip(tr("Conecta con estos datos y se desconecta, sin guardar nada"))
        self.test_button.clicked.connect(self.test)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)
        self.test_result.hide()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Conectar") if quick else tr("Aceptar"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancelar"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.test_button)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.test_result)
        layout.addWidget(self.notice)
        layout.addLayout(bottom)
        self.auth.currentIndexChanged.connect(self._auth_changed)
        self._auth_changed()
        self.notice.hide()

    def _auth_changed(self) -> None:
        kind = self.auth.currentData()
        self.key_file.setEnabled(kind == "key")
        self.save_password.setText(tr("Guardar la frase de la clave en el llavero del sistema") if kind == "key"
                                   else tr("Guardar la contraseña en el llavero del sistema"))
        self.save_password.setEnabled(kind != "agent")
        self.password.setEnabled(kind != "agent")
        self.password_label.setText(tr("Frase de la clave:") if kind == "key" else tr("Contraseña:"))

    def _choose_key(self) -> None:
        start = os.path.expanduser(self.key_file.text().strip() or "~/.ssh")
        chosen, _filter = QFileDialog.getOpenFileName(self, tr("Clave privada"), start if os.path.exists(start) else os.path.expanduser("~"))
        if chosen:
            self.key_file.setText(chosen)

    def typed_password(self) -> str:
        return self.password.text() if self.auth.currentData() != "agent" else ""

    def profile(self) -> remote.Profile | None:
        host = self.host.text().strip()
        name = self.name.text().strip() or f"{self.user.text().strip()}@{host}"
        return remote.profile_from({"name": name, "host": host, "port": self.port.value(), "user": self.user.text().strip(), "auth": self.auth.currentData(),
                                    "key_file": self.key_file.text().strip(), "folder": self.folder.text().strip(),
                                    "save_password": self.save_password.isChecked()})

    def _problem(self, profile: remote.Profile | None) -> str:
        if profile is None:
            return tr("Faltan el servidor o el usuario, o tienen espacios o caracteres que no pueden llevar")
        if profile.auth == "key" and not profile.key_file:
            return tr("Elige el archivo de la clave privada")
        return ""

    def _accept(self) -> None:
        profile = self.profile()
        problem = self._problem(profile)
        if not problem and (not self.save_profile.isVisible() or self.save_profile.isChecked()) and profile.name.casefold() in self._taken:
            problem = tr("Ya hay un perfil con ese nombre")
        if not problem:
            self.accept()
            return
        self.notice.setText(problem)
        self.notice.show()

    # ---------------------------------------------------------------- probar
    def _password_for_test(self, profile: remote.Profile) -> str | None:
        """La escrita; si no, la que ya se conoce de ese mismo servidor."""
        if profile.auth == "agent":
            return None
        typed = self.typed_password()
        if typed:
            return typed
        if self._stored is not None and self._stored.server == profile.server and self._stored.save_password:
            return _session_passwords.get(profile.server) or remote.stored_password(profile.server)
        return _session_passwords.get(profile.server)

    def test(self) -> None:
        profile = self.profile()
        problem = self._problem(profile)
        if problem:
            self._show_test(problem, ok=False)
            return
        self.notice.hide()
        self.test_button.setEnabled(False)
        self._show_test(tr("Probando la conexión con {host}…", host=profile.host), ok=None)
        password = self._password_for_test(profile)
        run_bg(lambda _sink: remote.probe(profile, password), on_done=lambda home: self._tested(profile, home, None),
               on_error=lambda exc: self._tested(profile, "", exc))

    def _tested(self, profile: remote.Profile, home: str, exc) -> None:
        if not shiboken6.isValid(self):
            return
        self.test_button.setEnabled(True)
        if exc is None:
            self._show_test(tr("✔ Conexión correcta. La carpeta personal en el servidor es {home}", home=home), ok=True)
        elif isinstance(exc, remote.UnknownHostKey):
            question = tr("No se conoce el servidor {host}.\n\nSu huella {kind} es:\n{fingerprint}\n\n"
                          "Si coincide con la que te ha dado su administrador, puedes confiar en él. Se añadirá a ~/.ssh/known_hosts. ¿Seguir con la prueba?",
                          host=remote.host_entry(exc.host, exc.port), kind=exc.key_type, fingerprint=exc.fingerprint)
            answer = QMessageBox.question(self, tr("Servidor desconocido"), question,
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self._show_test(tr("✘ Sin comprobar: no se confió en el servidor"), ok=False)
                return
            try:
                remote.accept_host_key(exc.host, exc.port, exc.key)
            except OSError as error:
                self._show_test(tr("No se pudo guardar en known_hosts: {exc}", exc=error), ok=False)
                return
            self.test()
        elif isinstance(exc, remote.AuthenticationNeeded):
            if not exc.wrong:
                message = (tr("✘ El servidor responde, pero falta la frase de la clave: escríbela para probarla") if profile.auth == "key"
                           else tr("✘ El servidor responde, pero falta la contraseña: escríbela para probarla"))
            elif profile.auth == "key":
                message = tr("✘ El servidor responde, pero no acepta la clave o su frase")
            else:
                message = tr("✘ El servidor responde, pero no acepta la contraseña")
            self._show_test(message, ok=False)
        else:
            self._show_test(tr("✘ {error}", error=str(exc) or exc.__class__.__name__), ok=False)

    def _show_test(self, text: str, ok: bool | None) -> None:
        self.test_result.setText(text)
        self.test_result.setStyleSheet("" if ok is None else f"color: {'#2e9e2e' if ok else '#d04040'};")
        self.test_result.show()
        layout = self.layout()
        # Qt no agranda solo un cuadro ya abierto por un texto que se parte en varias líneas.
        needed = layout.totalHeightForWidth(self.width()) if layout.hasHeightForWidth() else layout.totalSizeHint().height()
        if needed > self.height():
            self.resize(self.width(), needed)


# ------------------------------------------------------------------ la página de las preferencias
class SftpPage(QWidget):
    """Los perfiles guardados y qué se hace con los archivos de un servidor."""

    def __init__(self, config) -> None:
        super().__init__()
        self._profiles = profiles(config)
        self._forgotten: set[str] = set()
        self._new_passwords: dict[str, tuple[str, bool]] = {}  # se guardan al aplicar: cancelar no debe dejar nada en el llavero
        self.list = QListWidget()
        for profile in self._profiles:
            self._add_item(profile)
        add = QPushButton(tr("Nuevo…"))
        add.clicked.connect(self.add)
        self.edit_button = QPushButton(tr("Editar…"))
        self.edit_button.clicked.connect(self.edit)
        self.remove_button = QPushButton(tr("Eliminar"))
        self.remove_button.clicked.connect(self.remove)
        self.forget_button = QPushButton(tr("Olvidar la contraseña"))
        self.forget_button.setToolTip(tr("La borra del llavero del sistema: se volverá a pedir al conectar"))
        self.forget_button.clicked.connect(self.forget)
        buttons = QVBoxLayout()
        for button in (add, self.edit_button, self.remove_button, self.forget_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(self.list, 1)
        row.addLayout(buttons)
        self.open_files = QCheckBox(tr("Abrir los archivos del servidor con su programa"))
        self.open_files.setToolTip(tr("Se descargan a una carpeta temporal. Al guardar un cambio, se sube al servidor"))
        self.open_files.setChecked(open_files(config))
        self.upload = QComboBox()
        self.upload.addItem(tr("Preguntar antes de subirlo"), "ask")
        self.upload.addItem(tr("Subirlo sin preguntar"), "auto")
        self.upload.setCurrentIndex(1 if uploads_without_asking(config) else 0)
        self.upload.setToolTip(tr("Si el archivo ha cambiado en el servidor desde que lo abriste, se pregunta siempre"))
        self.open_files.toggled.connect(self.upload.setEnabled)
        self.upload.setEnabled(self.open_files.isChecked())
        upload_row = QHBoxLayout()
        upload_row.addSpacing(24)
        upload_row.addWidget(QLabel(tr("Al guardar un cambio:")))
        upload_row.addWidget(self.upload)
        upload_row.addStretch(1)
        self.previews = QCheckBox(tr("Vista previa y miniaturas de los archivos del servidor"))
        self.previews.setToolTip(tr("Descarga cada archivo que se mira: con una conexión lenta, mejor desactivado"))
        self.previews.setChecked(previews(config))
        self.preview_mb = QSpinBox()
        self.preview_mb.setRange(1, 1024)
        self.preview_mb.setSuffix(" MB")
        self.preview_mb.setValue(preview_limit(config) // (1024 * 1024))
        self.preview_mb.setToolTip(tr("Los archivos más grandes no se descargan para la vista previa"))
        self.previews.toggled.connect(self.preview_mb.setEnabled)
        self.preview_mb.setEnabled(self.previews.isChecked())
        preview_row = QHBoxLayout()
        preview_row.addWidget(self.previews)
        preview_row.addWidget(QLabel(tr("hasta")))
        preview_row.addWidget(self.preview_mb)
        preview_row.addStretch(1)
        hint = QLabel(tr("Las contraseñas se guardan en el llavero del sistema, nunca en la configuración. Las claves de los servidores "
                         "se comprueban con ~/.ssh/known_hosts, y las que aceptes se añaden ahí."))
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("Perfiles de conexión, para los paneles remotos (SFTP) y las terminales (SSH):")))
        layout.addLayout(row, 1)
        layout.addWidget(self.open_files)
        layout.addLayout(upload_row)
        layout.addLayout(preview_row)
        layout.addWidget(hint)
        self.list.currentRowChanged.connect(self._update_buttons)
        self.list.itemDoubleClicked.connect(lambda _item: self.edit())
        self.list.setCurrentRow(0)
        self._update_buttons()

    def _add_item(self, profile: remote.Profile) -> None:
        item = QListWidgetItem(f"{profile.name}  —  {profile.server}")
        self.list.addItem(item)

    def _update_buttons(self, *_args) -> None:
        chosen = self.list.currentRow() >= 0
        for button in (self.edit_button, self.remove_button, self.forget_button):
            button.setEnabled(chosen)

    def add(self) -> None:
        dialog = ProfileDialog(taken=[profile.name for profile in self._profiles], parent=self)
        if dialog.exec():
            profile = dialog.profile()
            self._remember_password(profile, dialog.typed_password())
            self._profiles.append(profile)
            self._add_item(profile)
            self.list.setCurrentRow(self.list.count() - 1)

    def edit(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        old = self._profiles[row]
        dialog = ProfileDialog(old, taken=[profile.name for index, profile in enumerate(self._profiles) if index != row], parent=self)
        if dialog.exec():
            new = dialog.profile()
            if new.server != old.server or not new.save_password:
                self._forgotten.add(old.server)
            self._remember_password(new, dialog.typed_password())
            self._profiles[row] = new
            self.list.item(row).setText(f"{new.name}  —  {new.server}")

    def remove(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self._forgotten.add(self._profiles.pop(row).server)
            self.list.takeItem(row)
            self._update_buttons()

    def forget(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self._forgotten.add(self._profiles[row].server)

    def _remember_password(self, profile: remote.Profile, password: str) -> None:
        if password:
            self._forgotten.discard(profile.server)
            self._new_passwords[profile.server] = (password, profile.save_password)

    def values(self) -> list[dict]:
        return [profile.to_dict() for profile in self._profiles]

    def apply_to(self, config) -> None:
        config[PROFILES_KEY] = self.values()
        config[OPEN_KEY] = self.open_files.isChecked()
        config[UPLOAD_KEY] = self.upload.currentData()
        config[PREVIEW_KEY] = self.previews.isChecked()
        config[PREVIEW_MB_KEY] = self.preview_mb.value()
        for server in self._forgotten:
            _session_passwords.pop(server, None)
            remote.forget_password(server)
        self._forgotten.clear()
        for server, (password, save) in self._new_passwords.items():
            _session_passwords[server] = password  # sin llavero, al menos hasta cerrar el programa
            if save:
                remote.store_password(server, password)
        self._new_passwords.clear()


# ------------------------------------------------------------------ conectar
class RemoteConnector(QObject):
    """Conecta en segundo plano y pregunta en la ventana lo que haga falta: si se confía en un servidor nuevo y la contraseña."""

    def __init__(self, window, report) -> None:
        super().__init__(window)
        self.window = window
        self.report = report

    def open(self, profile: remote.Profile, done, password: str | None = None, typed: bool = False) -> None:
        server = profile.server
        if password is None and profile.auth != "agent":
            password = _session_passwords.get(server) or (remote.stored_password(server) if profile.save_password else None)
        self.window.statusBar().showMessage(tr("Conectando con {server}…", server=profile.host), 15000)
        run_bg(lambda _sink: remote.connect(profile, password),
               on_done=lambda connection: self._connected(profile, password, typed, connection, done),
               on_error=lambda exc: self._failed(profile, password, exc, done))

    def _connected(self, profile, password, typed: bool, connection, done) -> None:
        self.window.statusBar().showMessage(tr("Conectado con {server}", server=profile.host), 4000)
        if typed and password:
            _session_passwords[profile.server] = password
            if profile.save_password and not remote.store_password(profile.server, password):
                self.report(tr("No hay un llavero del sistema donde guardar la contraseña: se recordará hasta cerrar el programa"))
        done(connection)

    def _failed(self, profile, password, exc, done) -> None:
        self.window.statusBar().clearMessage()
        if isinstance(exc, remote.UnknownHostKey):
            question = tr("No se conoce el servidor {host}.\n\nSu huella {kind} es:\n{fingerprint}\n\n"
                          "Si coincide con la que te ha dado su administrador, puedes confiar en él. Se añadirá a ~/.ssh/known_hosts. ¿Conectar?",
                          host=remote.host_entry(exc.host, exc.port), kind=exc.key_type, fingerprint=exc.fingerprint)
            answer = QMessageBox.question(self.window, tr("Servidor desconocido"), question,
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                remote.accept_host_key(exc.host, exc.port, exc.key)
            except OSError as error:
                QMessageBox.warning(self.window, tr("Servidor desconocido"), tr("No se pudo guardar en known_hosts: {exc}", exc=error))
                return
            self.open(profile, done, password, typed=False)
            return
        if isinstance(exc, remote.AuthenticationNeeded):
            if exc.wrong:
                _session_passwords.pop(profile.server, None)
            label = (tr("Frase de la clave {key}:", key=profile.key_file) if profile.auth == "key"
                     else tr("Contraseña de {server}:", server=f"{profile.user}@{profile.host}"))
            if exc.wrong:
                label = tr("No es correcta. {label}", label=label)
            text, ok = QInputDialog.getText(self.window, profile.name, label, QLineEdit.EchoMode.Password)
            if ok and text:
                self.open(profile, done, text, typed=True)
            return
        QMessageBox.warning(self.window, profile.name, str(exc) or exc.__class__.__name__)


# ------------------------------------------------------------------ abrir y previsualizar
class RemoteFiles(QObject):
    """Las copias locales de los archivos de un servidor: para abrirlos con su programa, subiendo los cambios si se quiere,
    y para la vista previa, que se borran al cerrar."""
    changed = Signal(str, str)  # (copia local, ruta remota): se ha guardado un cambio

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.folder = tempfile.mkdtemp(prefix="directory-commander-tux-tux-sftp-")
        self._opened: dict[str, str] = {}  # copia local → ruta remota
        self._versions: dict[str, tuple] = {}  # copia local → (fecha, tamaño) del archivo del servidor cuando se descargó o se subió
        self._previews: dict[tuple[str, float], str] = {}
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._file_changed)
        self._pending: set[str] = set()
        self._timer = QTimer(self, singleShot=True, interval=700)  # un programa guarda en varios pasos: se espera a que acabe
        self._timer.timeout.connect(self._settle)

    def _local_for(self, path: str, kind: str) -> str:
        folder = tempfile.mkdtemp(prefix=kind, dir=self.folder)
        return os.path.join(folder, remote.name_of(path))

    def fetch_for_opening(self, path: str, done, failed) -> None:
        local = self._local_for(path, "abrir-")

        def work(_sink):
            remote.download(path, local)
            found = remote.stat_entry(path)
            self._versions[local] = (found.mtime, found.size)
            return local

        def downloaded(local_path: str) -> None:
            self._opened[local_path] = path
            self._watcher.addPath(local_path)
            done(local_path)

        run_bg(work, on_done=downloaded, on_error=failed)

    def fetch_for_preview(self, entry, limit: int, done) -> None:
        """La copia, si ya se descargó esa versión, o se descarga. `done(ruta local)` solo si el archivo cabe en el límite."""
        if entry.is_dir or entry.size > limit:
            return
        key = (entry.path, entry.mtime)
        if key in self._previews and os.path.exists(self._previews[key]):
            done(self._previews[key])
            return
        local = self._local_for(entry.path, "ver-")

        def work(_sink):
            remote.download(entry.path, local)
            return local

        def downloaded(local_path: str) -> None:
            self._previews[key] = local_path
            done(local_path)

        run_bg(work, on_done=downloaded, on_error=lambda _exc: None)

    def _file_changed(self, local: str) -> None:
        if local in self._opened:
            if os.path.exists(local) and local not in self._watcher.files():
                self._watcher.addPath(local)  # algunos programas guardan sustituyendo el archivo: hay que volver a vigilarlo
            self._pending.add(local)
            self._timer.start()

    def _settle(self) -> None:
        pending, self._pending = self._pending, set()
        for local in sorted(pending):
            if os.path.exists(local):
                self.changed.emit(local, self._opened[local])

    def upload(self, local: str, done, failed, force: bool = False) -> None:
        """Sube la copia. Sin `force`, antes se comprueba que el del servidor siga como cuando se descargó: si no, falla con RemoteChanged."""
        path = self._opened.get(local)
        if path is None:
            return

        def work(_sink):
            if not force and local in self._versions:
                found = remote.stat_entry(path)
                if (found.mtime, found.size) != self._versions[local]:
                    raise RemoteChanged(tr("{name} ha cambiado en el servidor desde que lo abriste", name=remote.name_of(path)))
            remote.upload(local, path)
            found = remote.stat_entry(path)
            self._versions[local] = (found.mtime, found.size)

        run_bg(work, on_done=lambda _value: done(path), on_error=failed)

    def remote_path_of(self, local: str) -> str | None:
        return self._opened.get(local)

    def dispose(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)
