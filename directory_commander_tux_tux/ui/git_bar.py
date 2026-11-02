"""Git en la barra de una terminal: la rama, botones para lo más común y ventanas que arman la orden antes de enviarla."""
from __future__ import annotations

import shiboken6
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton,
                               QSizePolicy, QToolBar, QToolButton, QVBoxLayout, QWidget)

from ..core import bar_buttons, git_tools
from ..core.i18n import N_, tr
from ..core.shells import OTHER
from .background import run_bg
from .icons import themed

POLL_MS = 2000
BRANCH_MAX_WIDTH = 150
USER_MAX_WIDTH = 110
USER_LABEL_MARGINS = 10
USER_MIN_WIDTH = 50  # con menos sitio el nombre no se reconocería: queda solo el icono, con el nombre en su ayuda
USER_ICON_WIDTH = 24
MIN_TOOLS_WIDTH = 170  # lo que se deja siempre a los botones: unos tres y el desplegable con el resto
MAX_COMMAND_LENGTH = 7000  # cmd no admite líneas de más de 8191 caracteres y cortaría la orden
BUSY_NOTICE = N_("La terminal está ocupada o tiene algo escrito. Espera a que termine y vuelve a intentarlo.")
LABELS = {"status": N_("Status"), "add": N_("Add…"), "unstage": N_("Unstage…"), "commit": N_("Commit…"), "push": N_("Push"), "pull": N_("Pull"), "fetch": N_("Fetch"), "diff": N_("Diff"), "log": N_("Log")}
TIPS = {"add": N_("Elegir qué archivos añadir"), "unstage": N_("Quitar archivos de los añadidos con Add, sin perder sus cambios"), "commit": N_("Escribir el mensaje y elegir los archivos del commit")}


class _CommandDialog(QDialog):
    """Una ventanita con la orden que se va armando. Al aceptar se envía a la terminal; si está ocupada, se dice y se puede volver a intentar."""

    def __init__(self, title: str, send, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._send = send
        self.preview = QLineEdit()
        self.preview.setReadOnly(True)
        self.notice = QLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Aceptar"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancelar"))
        self.buttons.accepted.connect(self.submit)
        self.buttons.rejected.connect(self.reject)

    def command(self) -> str | None:
        raise NotImplementedError

    def refresh(self) -> None:
        command = self.command()
        too_long = bool(command) and len(command) > MAX_COMMAND_LENGTH
        self.preview.setText(command or "")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(command) and not too_long)
        missing = self.missing()
        if missing:
            self.notice.setText(tr(missing))
        elif too_long:
            self.notice.setText(tr("Son demasiados archivos para una sola orden. Elige menos."))
        elif self.has_input() and not command:
            self.notice.setText(tr("Esto no se puede escribir sin riesgo en esta terminal, por sus comillas o caracteres especiales. Hazlo a mano o usa otro intérprete."))
        self.notice.setVisible(bool(missing) or too_long or (self.has_input() and not command))

    def missing(self) -> str:
        """Lo que falta para poder aceptar, si hay que decirlo."""
        return ""

    def has_input(self) -> bool:
        raise NotImplementedError

    def submit(self) -> None:
        command = self.command()
        if not command or len(command) > MAX_COMMAND_LENGTH:
            return
        if self._send(command):
            self.accept()
        else:
            self.notice.setText(tr(BUSY_NOTICE))
            self.notice.show()


class _FileList(QWidget):
    """Los archivos con su casilla, y los botones para marcarlos todos o ninguno."""

    def __init__(self, changes: list[git_tools.Change], title: str, changed, checked=lambda _change: False) -> None:
        super().__init__()
        self.files = QListWidget()
        for change in changes:
            item = QListWidgetItem(change.label)
            item.setData(Qt.ItemDataRole.UserRole, change)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if checked(change) else Qt.CheckState.Unchecked)
            self.files.addItem(item)
        self.files.itemChanged.connect(lambda _item: changed())
        self.all_button = QPushButton(tr("Marcar todos"))
        self.none_button = QPushButton(tr("Desmarcar todos"))
        self.all_button.clicked.connect(lambda: self.mark_all(True))
        self.none_button.clicked.connect(lambda: self.mark_all(False))
        row = QHBoxLayout()
        row.addWidget(self.all_button)
        row.addWidget(self.none_button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(title))
        layout.addWidget(self.files, 1)
        layout.addLayout(row)

    def mark_all(self, marked: bool) -> None:
        for row in range(self.files.count()):
            self.files.item(row).setCheckState(Qt.CheckState.Checked if marked else Qt.CheckState.Unchecked)

    def chosen(self) -> list[git_tools.Change]:
        return [self.files.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.files.count())
                if self.files.item(row).checkState() == Qt.CheckState.Checked]


class FilesDialog(_CommandDialog):
    """Una orden seguida de los archivos que se marquen."""

    def __init__(self, changes: list[git_tools.Change], family: str, send, parent=None, base: str = git_tools.DEFAULT_COMMANDS[git_tools.ADD], renames_too: bool = False) -> None:
        super().__init__(base, send, parent)
        self.family, self.base, self.renames_too = family, base, renames_too
        self.list = _FileList(changes, tr("Archivos con cambios:"), self.refresh)
        self.files = self.list.files
        self.mark_all = self.list.mark_all
        layout = QVBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addWidget(QLabel(tr("Orden:")))
        layout.addWidget(self.preview)
        layout.addWidget(self.notice)
        layout.addWidget(self.buttons)
        self.resize(560, 380)
        self.refresh()

    def chosen(self) -> list[str]:
        """Para quitar un renombrado de lo añadido hacen falta sus dos nombres. Al añadirlo no, porque ya está hecho el cambio."""
        return [path for change in self.list.chosen() for path in (change.paths if self.renames_too else [change.path])]

    def has_input(self) -> bool:
        return bool(self.chosen())

    def command(self) -> str | None:
        return git_tools.files_command(self.base, self.chosen(), self.family)


class CommitDialog(_CommandDialog):
    """Lo que ya está añadido sale marcado. Si lo marcado coincide con ello, basta un commit normal. Si no, la orden nombra los archivos, y añade antes los que git aún no conoce."""

    def __init__(self, changes: list[git_tools.Change], family: str, send, parent=None, base: str = git_tools.DEFAULT_COMMANDS[git_tools.COMMIT], add_base: str = git_tools.DEFAULT_COMMANDS[git_tools.ADD]) -> None:
        super().__init__(base, send, parent)
        self.family, self.base, self.add_base = family, base, add_base
        self.changes = changes
        self.list = _FileList(changes, tr("Archivos que se incluyen:"), self.refresh, lambda change: change.staged)
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText(tr("Mensaje del commit"))
        self.message.textChanged.connect(self.refresh)
        self.all_changes = QCheckBox(tr("Incluir todos los archivos modificados (-a)"))
        self.amend = QCheckBox(tr("Corregir el último commit (--amend)"))
        self.all_changes.toggled.connect(lambda _checked: self.refresh())
        self.amend.toggled.connect(lambda _checked: self.refresh())
        self.all_changes.toggled.connect(self.list.setDisabled)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("Mensaje:")))
        layout.addWidget(self.message, 1)
        layout.addWidget(self.all_changes)
        layout.addWidget(self.list, 2)
        layout.addWidget(self.amend)
        layout.addWidget(QLabel(tr("Orden:")))
        layout.addWidget(self.preview)
        layout.addWidget(self.notice)
        layout.addWidget(self.buttons)
        self.resize(600, 520)
        self.message.setFocus()
        self.refresh()

    def has_input(self) -> bool:
        return bool(self.message.toPlainText().strip())

    def _no_files(self) -> bool:
        return not self.all_changes.isChecked() and not self.amend.isChecked() and not self.list.chosen()

    def missing(self) -> str:
        return N_("Marca al menos un archivo para el commit.") if self.has_input() and self._no_files() else ""

    def command(self) -> str | None:
        if self._no_files():
            return None
        chosen = self.list.chosen()
        staged = [change for change in self.changes if change.staged]
        named = chosen if not self.all_changes.isChecked() and chosen != staged else None
        # Corregir solo el mensaje: sin esto, se metería también en el commit lo añadido aunque se haya desmarcado.
        base = f"{self.base} --only" if named == [] else self.base
        return git_tools.commit_command(self.message.toPlainText(), self.family, self.amend.isChecked(), self.all_changes.isChecked(),
                                        [path for change in named for path in change.paths] if named else None,
                                        [change.path for change in named if change.untracked] if named else None, base, self.add_base)


class GitBar(QWidget):
    """Su contenido solo se ve si hay git, está activado y la carpeta de la terminal es un repositorio. Los botones que no caben pasan al desplegable de la barra."""

    def __init__(self, terminal, config, report=None, parent=None) -> None:
        super().__init__(parent)
        self.terminal, self.config = terminal, config
        self._report = report or (lambda _text: None)
        self.branch = ""
        self.user = ("", "")
        self._git = ""
        self._checking = False
        self.branch_button = QToolButton()
        self.branch_button.setObjectName("gitBranch")
        self.branch_button.setAutoRaise(True)
        self.branch_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.user_label = QLabel()
        self.user_label.setObjectName("gitUser")
        self.branch_menu = QMenu(self)
        self.branch_menu.aboutToShow.connect(self._fill_branches)
        self.branch_button.setMenu(self.branch_menu)
        self.tools = QToolBar()
        self.tools.setMovable(False)
        self.tools.setFloatable(False)
        self.tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.tools.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # no pide sitio: usa el que sobre y lo demás va al desplegable
        self.commands: dict[str, QAction] = {}
        self.content = QWidget()
        row = QHBoxLayout(self.content)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(1)
        row.addWidget(self.branch_button)
        row.addWidget(self.user_label)
        row.addWidget(self.tools, 1)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.content)
        self.content.hide()
        self.apply_settings()
        self._timer = QTimer(self, interval=POLL_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        terminal.started.connect(self.check)
        terminal.finished.connect(lambda: self._show_branch(None))

    def _custom(self, key: str) -> dict | None:
        return next((item for item in git_tools.clean_custom(self.config.get("git_custom_commands")) if item["key"] == key), None)

    def _make_action(self, key: str) -> QAction:
        action = QAction(self.tools)  # de la barra que las muestra: si se destruyeran por separado, Qt podría caerse
        action.triggered.connect(lambda _=False, k=key: self.run(k))
        self.commands[key] = action
        return action

    def apply_settings(self) -> None:
        """Qué botones se ven, en qué orden y qué ejecutan, según las preferencias."""
        removed = bar_buttons.hidden(self.config, bar_buttons.GIT)
        self.tools.clear()
        keys = bar_buttons.arranged(self.config, bar_buttons.GIT)
        for key in set(self.commands) - set(keys):
            self.commands.pop(key).deleteLater()  # una orden añadida que se ha eliminado
        for key in keys:
            action = self.commands.get(key) or self._make_action(key)
            custom = self._custom(key)
            command = git_tools.command_of(self.config, key)
            action.setText(custom["label"] if custom else tr(LABELS[key]))
            action.setToolTip(f"{tr(TIPS[key])}: {command}" if key in TIPS else command)
            if key not in removed:
                self.tools.addAction(action)
        self.branch_button.setVisible(bool(self.config.get("git_show_branch", True)))
        self._fit_user()
        self.check()

    def _fit_user(self) -> None:
        """El usuario, acortado o solo con su icono si hace falta, pero sin quitarles a los botones su sitio. Sin sitio ni para eso, sigue en la ayuda de la rama."""
        name, email = self.user
        metrics = self.user_label.fontMetrics()
        spare = self.width() - self.branch_button.sizeHint().width() - MIN_TOOLS_WIDTH
        room = spare - metrics.horizontalAdvance("👤 ") - USER_LABEL_MARGINS
        if room < USER_MIN_WIDTH:
            self.user_label.setText("👤")
        else:
            self.user_label.setText(f"👤 {metrics.elidedText(name or email, Qt.TextElideMode.ElideRight, min(room, USER_MAX_WIDTH))}" if name or email else tr("👤 sin usuario"))
        self.user_label.setVisible(bool(self.config.get("git_show_user", True)) and spare >= USER_ICON_WIDTH)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_user()

    def _tick(self) -> None:
        if self.window().isActiveWindow():  # con la ventana en segundo plano no hace falta estar lanzando git
            self.check()

    def check(self) -> None:
        """Mira en segundo plano si git sigue instalado y si la carpeta es un repositorio. Se repite porque también se puede cambiar de rama escribiendo."""
        terminal = self.terminal
        if terminal.session is None or terminal.family == OTHER or not self.config.get("git_enabled", True):
            self._show_branch(None)
            return
        if self._checking or not terminal.isVisible():
            return
        self._checking = True
        configured, folder = self.config.get("git_program", ""), terminal.current_path()

        wants_user = bool(self.config.get("git_show_user", True))

        def find(_sink) -> tuple[str, str | None, tuple[str, str]]:
            git = git_tools.git_program(configured)
            branch = git_tools.current_branch(git, folder) if git else None
            return git, branch, git_tools.current_user(git, folder) if branch and wants_user else ("", "")

        def done(found) -> None:
            if shiboken6.isValid(self):  # la ventana se ha podido cerrar mientras tanto
                self._checking = False
                self._git = found[0]
                self._show_branch(found[1])
                self._show_user(*found[2])

        def failed(_exc) -> None:
            if shiboken6.isValid(self):
                self._checking = False

        run_bg(find, done, failed)

    def _show_branch(self, branch: str | None) -> None:
        self.branch = branch or ""
        metrics = self.branch_button.fontMetrics()
        self.branch_button.setText(metrics.elidedText(f"⎇ {branch}", Qt.TextElideMode.ElideMiddle, BRANCH_MAX_WIDTH) if branch else "")
        self._branch_tip()
        self.content.setVisible(bool(branch) and self.terminal.session is not None and bool(self.config.get("git_enabled", True)))

    def _branch_tip(self) -> None:
        tip = tr("Rama activa: {branch}. Púlsala para cambiar de rama", branch=self.branch) if self.branch else ""
        self.branch_button.setToolTip(f"{tip}\n{self.user_label.toolTip()}" if self.branch and self.config.get("git_show_user", True) else tip)

    def _show_user(self, name: str, email: str) -> None:
        self.user = (name, email)
        self.user_label.setToolTip(tr("Los commits se firman como: {user}", user=f"{name} <{email}>") if name or email else tr("Git no tiene configurado ningún usuario en esta carpeta"))
        self._branch_tip()
        self._fit_user()

    # ---------------------------------------------------------------- órdenes
    def send(self, command: str) -> bool:
        """Se ejecuta o, si se prefiere revisar, solo se escribe. False si la terminal no puede recibirla ahora."""
        sent = self.terminal.run_command(command, bool(self.config.get("git_run_commands", True)))
        if sent:
            self.terminal.setFocus()
        return sent

    def run(self, key: str) -> None:
        command = git_tools.command_of(self.config, key)
        custom = self._custom(key)
        if key == git_tools.ADD or (custom and custom["files"]):
            self.open_files(command, lambda change: change.can_add or bool(custom))
        elif key == git_tools.UNSTAGE:
            self.open_unstage(command)
        elif key == git_tools.COMMIT:
            self.open_commit(command)
        elif not self.send(command):
            self._report(tr(BUSY_NOTICE))

    def _with_changes(self, show) -> None:
        """Pide a git la lista en segundo plano y se la pasa a `show`."""
        git, folder = self._git, self.terminal.current_path()
        if not git:
            return

        def shown(changes) -> None:
            if shiboken6.isValid(self):
                show(changes)

        run_bg(lambda _sink: git_tools.changed_files(git, folder), shown, lambda exc: self._report(str(exc)))

    def open_files(self, command: str, wanted, renames_too: bool = False, nothing: str = N_("No hay archivos con cambios que añadir")) -> None:
        def show(changes) -> None:
            files = [change for change in changes if wanted(change)]
            if files:
                self._exec_dialog(FilesDialog(files, self.terminal.family, self.send, self.window(), command, renames_too))
            else:
                self._report(tr(nothing))

        self._with_changes(show)

    def open_unstage(self, command: str) -> None:
        git, folder = self._git, self.terminal.current_path()
        if not git:
            return

        def choose(commits: bool) -> None:
            if shiboken6.isValid(self):
                unchanged = command == git_tools.DEFAULT_COMMANDS[git_tools.UNSTAGE]
                self.open_files(command if commits or not unchanged else git_tools.UNSTAGE_WITHOUT_COMMITS, lambda change: change.staged, True, N_("No hay archivos añadidos que quitar"))

        run_bg(lambda _sink: git_tools.has_commits(git, folder), choose, lambda exc: self._report(str(exc)))

    def open_commit(self, command: str | None = None) -> None:
        base = command or git_tools.command_of(self.config, git_tools.COMMIT)
        add_base = git_tools.command_of(self.config, git_tools.ADD)
        self._with_changes(lambda changes: self._exec_dialog(CommitDialog(changes, self.terminal.family, self.send, self.window(), base, add_base)))

    def _exec_dialog(self, dialog: QDialog) -> None:
        dialog.exec()
        dialog.deleteLater()
        self.check()

    def _fill_branches(self) -> None:
        menu = self.branch_menu
        menu.clear()
        git, folder = self._git, self.terminal.current_path()
        for name in git_tools.branches(git, folder) if git else []:
            action = menu.addAction(name, lambda n=name: self._switch(n))
            if name == self.branch:
                font = action.font()
                font.setBold(True)
                action.setFont(font)
        menu.addSeparator()
        menu.addAction(themed("plus"), tr("Nueva rama…"), self._new_branch)

    def _switch(self, name: str, create: bool = False) -> None:
        command = git_tools.switch_command(name, self.terminal.family, create)
        if command is None:
            self._report(tr("Ese nombre de rama no se puede escribir sin riesgo en esta terminal"))
        elif not self.send(command):
            self._report(tr(BUSY_NOTICE))

    def _new_branch(self) -> None:
        name, accepted = QInputDialog.getText(self, tr("Nueva rama"), tr("Nombre de la rama:"))
        if accepted and name.strip():
            self._switch(name.strip(), True)
