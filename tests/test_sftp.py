"""Paneles remotos por SFTP: rutas, perfiles, contraseñas, claves de los servidores y operaciones, contra un servidor de pruebas."""
import os
import time

import pytest

pytest.importorskip("paramiko")

from directory_commander_tux_tux.core import fsops, remote, remote_ops  # noqa: E402
from directory_commander_tux_tux.core.fsops import Action, Answer, Collision, ProgressSink  # noqa: E402
from tests.sftp_server import PASSWORD, USER, SftpServer, host_key  # noqa: E402


class MemoryKeyring:
    """El llavero de las pruebas: el del sistema no se toca."""

    def __init__(self) -> None:
        import keyring.backend

        class Backend(keyring.backend.KeyringBackend):
            priority = 1
            stored: dict = {}

            def get_password(self, service, username):
                return self.stored.get((service, username))

            def set_password(self, service, username, password):
                self.stored[(service, username)] = password

            def delete_password(self, service, username):
                self.stored.pop((service, username), None)

        self.backend = Backend()
        self.backend.stored = {}


@pytest.fixture
def memory_keyring():
    keyring = pytest.importorskip("keyring")
    previous = keyring.get_keyring()
    fake = MemoryKeyring().backend
    keyring.set_keyring(fake)
    yield fake
    keyring.set_keyring(previous)


@pytest.fixture
def server(tmp_path):
    found = SftpServer(str(tmp_path / "servidor"))
    yield found
    remote.disconnect_all()
    found.close()


@pytest.fixture
def known_hosts(tmp_path):
    return str(tmp_path / "ssh" / "known_hosts")


def profile_for(server, **changes) -> remote.Profile:
    values = {"name": "Pruebas", "host": "127.0.0.1", "port": server.port, "user": USER}
    values.update(changes)
    return remote.profile_from(values)


@pytest.fixture
def connected(server, known_hosts):
    profile = profile_for(server)
    remote.accept_host_key("127.0.0.1", server.port, server.key, known_hosts)
    connection = remote.connect(profile, PASSWORD, known_hosts)
    return connection


def server_path(server, inner: str) -> str:
    return os.path.join(server.root, inner.lstrip("/"))


# ------------------------------------------------------------------ rutas
def test_remote_paths_keep_their_shape():
    path = remote.make_path("ana@ejemplo.org:22", "/var/www")
    assert path == "sftp://ana@ejemplo.org:22/var/www"
    assert remote.is_remote(path) and not remote.is_remote("/var/www")
    assert remote.server_of(path) == "ana@ejemplo.org:22" and remote.inner_of(path) == "/var/www"
    assert remote.join(path, "a b.txt") == "sftp://ana@ejemplo.org:22/var/www/a b.txt"
    assert remote.parent(path) == "sftp://ana@ejemplo.org:22/var" and remote.parent(remote.root(path)) == ""
    assert remote.normalize("sftp://ana@ejemplo.org:22//var/./www/../www/") == path
    assert remote.name_of(path) == "www" and remote.label(path) == "ana@ejemplo.org:/var/www"
    assert remote.crumbs(path) == [("ana@ejemplo.org", "sftp://ana@ejemplo.org:22/"), ("var", "sftp://ana@ejemplo.org:22/var"),
                                   ("www", path)]
    assert remote.is_inside(remote.join(path, "x"), path) and not remote.is_inside("sftp://ana@otro:22/var/www/x", path)
    assert remote.server_key("ana", "::1", 2222) == "ana@[::1]:2222"


def test_profiles_are_validated():
    raw = [{"name": "Web", "host": "ejemplo.org", "user": "ana"}, {"name": "web", "host": "otro", "user": "b"},
           {"name": "Mal puerto", "host": "x", "user": "y", "port": 0}, {"name": "Sin usuario", "host": "x"},
           {"name": "Raro", "host": "a b", "user": "c"}, "basura", {"name": "Llave", "host": "h", "user": "u", "auth": "key", "key_file": "~/.ssh/id"}]
    cleaned = remote.clean_profiles(raw)
    assert [item["name"] for item in cleaned] == ["Web", "Llave"]
    assert cleaned[0]["port"] == 22 and cleaned[0]["auth"] == "password" and cleaned[0]["save_password"] is True
    assert remote.find_profile(raw, "llave").server == "u@h:22"


def test_passwords_go_to_the_keyring(memory_keyring):
    assert remote.stored_password("ana@h:22") is None
    assert remote.store_password("ana@h:22", "secreto")
    assert remote.stored_password("ana@h:22") == "secreto"
    assert memory_keyring.stored == {(remote.KEYRING_SERVICE, "ana@h:22"): "secreto"}
    remote.forget_password("ana@h:22")
    assert remote.stored_password("ana@h:22") is None


def test_without_a_keyring_nothing_breaks(monkeypatch):
    import keyring

    def broken(*_args):
        raise RuntimeError("sin llavero")

    monkeypatch.setattr(keyring, "get_password", broken)
    monkeypatch.setattr(keyring, "set_password", broken)
    assert remote.stored_password("a@b:22") is None and remote.store_password("a@b:22", "x") is False


# ------------------------------------------------------------------ conectar
def test_an_unknown_server_is_asked_about_and_then_remembered(server, known_hosts):
    profile = profile_for(server)
    with pytest.raises(remote.UnknownHostKey) as caught:
        remote.connect(profile, PASSWORD, known_hosts)
    assert caught.value.fingerprint.startswith("SHA256:") and caught.value.port == server.port
    assert not os.path.exists(known_hosts)  # nada se guarda sin aceptarlo
    remote.accept_host_key(caught.value.host, caught.value.port, caught.value.key, known_hosts)
    with open(known_hosts, encoding="utf-8") as handle:
        assert handle.read().startswith(f"[127.0.0.1]:{server.port} ssh-rsa ")
    connection = remote.connect(profile, PASSWORD, known_hosts)
    assert connection.home == f"/home/{USER}"
    assert connection.start_path() == f"sftp://{USER}@127.0.0.1:{server.port}/home/{USER}"


def test_accepting_keeps_what_known_hosts_already_had(server, known_hosts):
    os.makedirs(os.path.dirname(known_hosts))
    with open(known_hosts, "w", encoding="utf-8") as handle:
        handle.write("# mis servidores\notro.org ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB\n")
    remote.accept_host_key("127.0.0.1", server.port, server.key, known_hosts)
    with open(known_hosts, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert lines[:2] == ["# mis servidores", "otro.org ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB"] and len(lines) == 3


def test_a_changed_server_key_is_refused(server, known_hosts):
    remote.accept_host_key("127.0.0.1", server.port, host_key(1), known_hosts)  # la que se conocía era otra
    with pytest.raises(OSError, match="ha cambiado"):
        remote.connect(profile_for(server), PASSWORD, known_hosts)


def test_a_missing_or_wrong_password_is_reported(server, known_hosts):
    remote.accept_host_key("127.0.0.1", server.port, server.key, known_hosts)
    with pytest.raises(remote.AuthenticationNeeded) as missing:
        remote.connect(profile_for(server), None, known_hosts)
    assert not missing.value.wrong
    with pytest.raises(remote.AuthenticationNeeded) as wrong:
        remote.connect(profile_for(server), "otra", known_hosts)
    assert wrong.value.wrong


def test_an_unreachable_server_gives_an_error(known_hosts):
    with pytest.raises(OSError):
        remote.connect(remote.profile_from({"name": "x", "host": "127.0.0.1", "port": 9, "user": "u"}), "x", known_hosts)


def test_without_connection_its_paths_cannot_be_read():
    with pytest.raises(OSError, match="No hay conexión"):
        remote.list_remote("sftp://nadie@ningun.sitio:22/")


def test_a_dropped_connection_reconnects_by_itself(server, connected):
    folder = connected.start_path()
    server.drop_connections()
    time.sleep(0.2)
    assert remote.list_remote(folder) == []


# ------------------------------------------------------------------ leer y operar
def test_listing_shows_folders_files_and_hidden_ones(server, connected):
    home = server_path(server, f"/home/{USER}")
    os.mkdir(os.path.join(home, "carpeta"))
    with open(os.path.join(home, "nota.txt"), "w") as handle:
        handle.write("hola")
    open(os.path.join(home, ".oculto"), "w").close()
    folder = connected.start_path()
    entries = {entry.name: entry for entry in remote.list_remote(folder)}
    assert set(entries) == {"carpeta", "nota.txt"}
    assert entries["carpeta"].is_dir and entries["nota.txt"].size == 4 and entries["nota.txt"].path == remote.join(folder, "nota.txt")
    assert ".oculto" in {entry.name for entry in remote.list_remote(folder, show_hidden=True)}
    from directory_commander_tux_tux.core.listing import list_dir
    assert {entry.name for entry in list_dir(folder)} == {"carpeta", "nota.txt"}  # el listado general también la entiende


def test_upload_download_and_copy_on_the_server(server, connected, tmp_path):
    local = tmp_path / "local"
    (local / "dentro").mkdir(parents=True)
    (local / "a.txt").write_text("uno")
    (local / "dentro" / "b.txt").write_text("dos")
    folder = connected.start_path()
    result = fsops.transfer([str(local / "a.txt"), str(local / "dentro")], folder)
    assert result.ok and result.done >= 2
    home = server_path(server, f"/home/{USER}")
    assert open(os.path.join(home, "a.txt")).read() == "uno" and open(os.path.join(home, "dentro", "b.txt")).read() == "dos"
    assert not any(name.endswith(fsops.PART_SUFFIX) for name in os.listdir(home))
    back = tmp_path / "vuelta"
    back.mkdir()
    result = fsops.transfer([remote.join(folder, "dentro")], str(back))
    assert result.ok and (back / "dentro" / "b.txt").read_text() == "dos"
    result = fsops.transfer([remote.join(folder, "a.txt")], folder)  # en la misma carpeta: una copia con otro nombre
    assert result.ok and open(os.path.join(home, "a (2).txt")).read() == "uno"


def test_moving_on_the_same_server_renames(server, connected):
    folder = connected.start_path()
    remote_ops.create_folder(folder, "destino")
    remote_ops.create_file(folder, "f.txt")
    result = fsops.transfer([remote.join(folder, "f.txt")], remote.join(folder, "destino"), move=True)
    assert result.ok
    home = server_path(server, f"/home/{USER}")
    assert os.path.exists(os.path.join(home, "destino", "f.txt")) and not os.path.exists(os.path.join(home, "f.txt"))


def test_moving_from_the_server_removes_the_original(server, connected, tmp_path):
    folder = connected.start_path()
    with open(server_path(server, f"/home/{USER}/m.txt"), "w") as handle:
        handle.write("m")
    result = fsops.transfer([remote.join(folder, "m.txt")], str(tmp_path), move=True)
    assert result.ok and (tmp_path / "m.txt").read_text() == "m"
    assert not os.path.exists(server_path(server, f"/home/{USER}/m.txt"))


@pytest.mark.parametrize("policy, expected", [(Collision.SKIP, "viejo"), (Collision.REPLACE, "nuevo"), (Collision.RENAME, "viejo")])
def test_collisions_follow_the_same_rules(server, connected, tmp_path, policy, expected):
    folder = connected.start_path()
    target = server_path(server, f"/home/{USER}/c.txt")
    with open(target, "w") as handle:
        handle.write("viejo")
    (tmp_path / "c.txt").write_text("nuevo")
    result = fsops.transfer([str(tmp_path / "c.txt")], folder, policy=policy)
    assert not result.errors and open(target).read() == expected
    if policy is Collision.RENAME:
        assert open(server_path(server, f"/home/{USER}/c (2).txt")).read() == "nuevo"


def test_asking_about_a_collision(server, connected, tmp_path):
    folder = connected.start_path()
    with open(server_path(server, f"/home/{USER}/p.txt"), "w") as handle:
        handle.write("viejo")
    (tmp_path / "p.txt").write_text("nuevo")
    asked = []

    def ask(src, dest):
        asked.append((src, dest))
        return Answer(Action.RENAME_NEW, name="elegido.txt")

    result = fsops.transfer([str(tmp_path / "p.txt")], folder, policy=Collision.ASK, ask=ask)
    assert result.ok and asked == [(str(tmp_path / "p.txt"), remote.join(folder, "p.txt"))]
    assert open(server_path(server, f"/home/{USER}/elegido.txt")).read() == "nuevo"
    cancelled = fsops.transfer([str(tmp_path / "p.txt")], folder, policy=Collision.ASK, ask=lambda _s, _d: None)
    assert cancelled.cancelled


def test_cancelling_leaves_no_half_file(server, connected, tmp_path):
    folder = connected.start_path()
    (tmp_path / "grande.bin").write_bytes(os.urandom(3 * fsops.CHUNK))
    sink = ProgressSink(lambda done, _total, _name: sink.cancel() if done else None)
    result = fsops.transfer([str(tmp_path / "grande.bin")], folder, sink=sink)
    assert result.cancelled
    assert os.listdir(server_path(server, f"/home/{USER}")) == []


def test_delete_create_and_rename(server, connected):
    folder = connected.start_path()
    created = fsops.create_folder(folder, "nueva")
    made = fsops.create_file(created, "vacío.txt")
    assert made == remote.join(created, "vacío.txt") and os.path.isfile(server_path(server, f"/home/{USER}/nueva/vacío.txt"))
    with pytest.raises(FileExistsError):
        fsops.create_file(created, "vacío.txt")
    renamed = fsops.rename_one(made, "lleno.txt")
    assert renamed == remote.join(created, "lleno.txt")
    with pytest.raises(ValueError):
        fsops.rename_one(renamed, "a/b")
    assert fsops.tree_size(created) == (0, 1)
    with pytest.raises(OSError, match="papelera"):
        fsops.delete_paths([created], permanent=False)
    result = fsops.delete_paths([created], permanent=True)
    assert result.ok and not os.path.exists(server_path(server, f"/home/{USER}/nueva"))


def test_the_server_root_is_never_deleted(connected):
    result = fsops.delete_paths([remote.root(connected.start_path())], permanent=True)
    assert result.errors and "raíz" in result.errors[0][1]


def test_drops_move_on_the_same_server_and_copy_between_machines(connected, tmp_path):
    folder = connected.start_path()
    other = remote.join(folder, "otra")
    assert fsops.drop_operation([remote.join(folder, "a")], other) == "move"
    assert fsops.drop_operation([str(tmp_path / "a")], folder) == "copy"
    assert fsops.drop_operation([remote.join(folder, "a")], str(tmp_path)) == "copy"
    assert fsops.drop_operation([remote.join(folder, "a")], str(tmp_path), force_move=True) == "move"
    assert fsops.drop_operation([remote.join(folder, "a")], folder) is None  # ya está ahí


def test_download_and_upload_of_one_file(server, connected, tmp_path):
    folder = connected.start_path()
    with open(server_path(server, f"/home/{USER}/d.txt"), "w") as handle:
        handle.write("contenido")
    local = tmp_path / "d.txt"
    remote.download(remote.join(folder, "d.txt"), str(local))
    assert local.read_text() == "contenido"
    assert remote.read_bytes(remote.join(folder, "d.txt"), 4) == b"cont"
    local.write_text("cambiado")
    remote.upload(str(local), remote.join(folder, "d.txt"))
    assert open(server_path(server, f"/home/{USER}/d.txt")).read() == "cambiado"


# ------------------------------------------------------------------ la terminal remota
def test_the_remote_terminal_starts_in_the_folder_and_carries_what_is_typed(server, connected):
    os.makedirs(server_path(server, "/srv/web"))
    received = []
    ended = []
    session = remote.RemoteShellSession([], remote.make_path(connected.server, "/srv/web"), 80, 24, received.append, lambda: ended.append(True))
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and b"[/srv/web]" not in b"".join(received):
            time.sleep(0.02)
        assert b"[/srv/web]" in b"".join(received)
        assert server.shells[-1].received.startswith(b" cd /srv/web\r")
        session.write(b"ls\r")
        session.resize(100, 30)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and b"ls" not in server.shells[-1].received:
            time.sleep(0.02)
        assert b"ls\r" in server.shells[-1].received and server.pty_sizes[0] == (80, 24)
        assert session.shell_is_idle() and session.current_directory() is None
    finally:
        session.close()
    assert ended == []  # cerrarla desde aquí no avisa de que terminó


# ------------------------------------------------------------------ en la ventana
from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QMenu, QApplication, QInputDialog, QMessageBox  # noqa: E402

from directory_commander_tux_tux.core.config import Config  # noqa: E402
from directory_commander_tux_tux.ui import sftp  # noqa: E402
from directory_commander_tux_tux.ui.drag_drop import dropped_paths, path_url  # noqa: E402
from directory_commander_tux_tux.ui.file_view import path_crumbs  # noqa: E402
from directory_commander_tux_tux.ui.main_window import MainWindow, _tab_title  # noqa: E402
from directory_commander_tux_tux.ui.terminal import TerminalView  # noqa: E402
from tests.test_terminal import FakeSession  # noqa: E402


def wait_until(condition, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


@pytest.fixture
def answers(monkeypatch):
    """Lo que contestaría el usuario: confiar en el servidor, la contraseña y sí a las confirmaciones."""
    asked = {"questions": [], "passwords": []}

    def question(_parent, _title, text, *_args):
        asked["questions"].append(text)
        return QMessageBox.StandardButton.Yes

    def get_text(_parent, _title, label, *_args, **_kwargs):
        asked["passwords"].append(label)
        return PASSWORD, True

    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QInputDialog, "getText", get_text)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: asked.setdefault("warnings", []).append(args[2]))
    return asked


@pytest.fixture
def window(tmp_path, monkeypatch, server, memory_keyring, answers):
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setattr(remote, "known_hosts_path", lambda: str(tmp_path / "ssh" / "known_hosts"))
    FakeSession.instances = []
    monkeypatch.setattr(TerminalView, "session_factory", FakeSession)  # las terminales del equipo, sin intérprete
    sftp._session_passwords.clear()
    left, right = tmp_path / "izquierda", tmp_path / "derecha"
    left.mkdir()
    right.mkdir()
    config = Config()
    config[sftp.PROFILES_KEY] = [{"name": "Pruebas", "host": "127.0.0.1", "port": server.port, "user": USER}]
    main = MainWindow(config, [str(left), str(right)])
    main.resize(1400, 800)
    main.show()
    QApplication.processEvents()
    main.left, main.right = left, right
    yield main
    main.close()
    main.deleteLater()
    QApplication.processEvents()


def connect_pane(window, side: int = 1):
    window._view_activated(window.groups[side].current)
    window.run_command("Set SFTP=Pruebas")
    view = window.groups[side].current
    assert wait_until(lambda: remote.is_remote(view.path))
    return view


def test_a_pane_becomes_remote_asking_only_the_first_time(window, server, answers, memory_keyring, tmp_path):
    with open(server_path(server, f"/home/{USER}/en el servidor.txt"), "w") as handle:
        handle.write("hola")
    view = connect_pane(window)
    assert view.path == f"sftp://{USER}@127.0.0.1:{server.port}/home/{USER}"
    assert wait_until(lambda: "en el servidor.txt" in {entry.name for entry in view.model.entries})
    assert len(answers["questions"]) == 1 and "SHA256:" in answers["questions"][0]  # la huella del servidor
    assert len(answers["passwords"]) == 1
    assert memory_keyring.stored == {(remote.KEYRING_SERVICE, f"{USER}@127.0.0.1:{server.port}"): PASSWORD}
    assert os.path.exists(tmp_path / "ssh" / "known_hosts")
    assert window.windowTitle().startswith(f"{USER}@127.0.0.1:{server.port}:/home/{USER}")
    assert [label for label, _target in path_crumbs(view.path)] == [f"{USER}@127.0.0.1:{server.port}", "home", USER]
    window.run_command("Set SFTP=Off")
    assert view.path == str(window.right)  # vuelve adonde estaba
    assert not remote.is_connected(f"{USER}@127.0.0.1:{server.port}")
    sftp._session_passwords.clear()
    connect_pane(window)  # la segunda vez, sin preguntar nada: la clave ya se conoce y la contraseña está en el llavero
    assert len(answers["questions"]) == 1 and len(answers["passwords"]) == 1


def test_up_and_root_stay_on_the_server(window, server):
    os.makedirs(server_path(server, f"/home/{USER}/sub"))
    view = connect_pane(window)
    view.navigate(remote.join(view.path, "sub"))
    view.up()
    assert view.path == f"sftp://{USER}@127.0.0.1:{server.port}/home/{USER}"
    view.go_root()
    assert view.path == f"sftp://{USER}@127.0.0.1:{server.port}/" and view.parent_path() == ""


def test_copying_both_ways_between_panes(window, server):
    (window.left / "local.txt").write_text("del equipo")
    view = connect_pane(window)
    window._view_activated(window.groups[0].current)
    window.groups[0].current.refresh()
    assert wait_until(lambda: "local.txt" in {entry.name for entry in window.groups[0].current.model.entries})
    window.groups[0].current.select_paths([str(window.left / "local.txt")])
    window.run_command("Copy")
    assert wait_until(lambda: not window.jobs.running and os.path.exists(server_path(server, f"/home/{USER}/local.txt")))
    with open(server_path(server, f"/home/{USER}/remoto.txt"), "w") as handle:
        handle.write("del servidor")
    window._view_activated(view)
    view.refresh()
    assert wait_until(lambda: "remoto.txt" in {entry.name for entry in view.model.entries})
    view.select_paths([remote.join(view.path, "remoto.txt")])
    window.run_command("Copy MOVE")
    assert wait_until(lambda: not window.jobs.running and (window.left / "remoto.txt").exists())
    assert (window.left / "remoto.txt").read_text() == "del servidor"
    assert not os.path.exists(server_path(server, f"/home/{USER}/remoto.txt"))
    assert window._undo == [] or all(entry.get("kind") != "transfer" for entry in window._undo)  # deshacer no sabe de servidores


def test_folders_files_rename_and_delete_on_the_server(window, server, answers):
    view = connect_pane(window)
    window.run_command('CreateFolder NAME="nueva"')  # en segundo plano: la ventana no espera al servidor
    assert wait_until(lambda: os.path.isdir(server_path(server, f"/home/{USER}/nueva")))
    window.run_command('CreateFile NAME="nota.txt"')
    assert wait_until(lambda: os.path.isfile(server_path(server, f"/home/{USER}/nota.txt")))
    assert wait_until(lambda: "nota.txt" in {entry.name for entry in view.model.entries})
    row = view.model.row_of(remote.join(view.path, "nota.txt"))
    assert view.model.setData(view.model.index(row, 0), "cambiada.txt")
    assert wait_until(lambda: os.path.isfile(server_path(server, f"/home/{USER}/cambiada.txt")))
    assert wait_until(lambda: view.model.row_of(remote.join(view.path, "cambiada.txt")) == row)
    view.refresh()
    assert wait_until(lambda: "cambiada.txt" in {entry.name for entry in view.model.entries})
    view.select_paths([remote.join(view.path, "cambiada.txt")])
    window.run_command("Delete")
    assert "definitivamente" in answers["questions"][-1]  # en un servidor no hay papelera
    assert wait_until(lambda: not window.jobs.running and not os.path.exists(server_path(server, f"/home/{USER}/cambiada.txt")))


def test_what_needs_local_files_is_refused_in_a_remote_pane(window, server):
    connect_pane(window)
    for command in ("GetSizes", "Properties", "Archive", "Clipboard COPY", "Find"):
        window.run_command(command)
        assert window.statusBar().currentMessage() == "No disponible en un panel remoto"
    menu = window.build_folder_menu(window.source)
    enabled = {action.text(): action.isEnabled() for action in menu.actions() if action.text()}
    assert enabled["Nueva carpeta"] and not enabled["Calcular tamaños de carpetas"] and not enabled["Propiedades de esta carpeta"]


def test_dragging_between_a_remote_and_a_local_pane(window, server):
    view = connect_pane(window)
    path = remote.join(view.path, "a b#%.txt")
    url = path_url(path)
    assert url.scheme() == "sftp" and not url.isLocalFile()
    mime = QMimeData()
    mime.setUrls([url, QUrl.fromLocalFile(str(window.left / "x"))])
    assert dropped_paths(mime) == [path, str(window.left / "x")]
    with open(server_path(server, f"/home/{USER}/arrastrado.txt"), "w") as handle:
        handle.write("x")
    window._files_dropped(window.groups[0].current, [remote.join(view.path, "arrastrado.txt")], str(window.left), False)
    assert wait_until(lambda: not window.jobs.running and (window.left / "arrastrado.txt").exists())


def test_the_terminal_of_a_remote_pane_runs_on_the_server(window, server):
    view = connect_pane(window)
    os.makedirs(server_path(server, f"/home/{USER}/web"))
    window.run_command("Set TERMINAL=On")
    terminal = window.terminals[1]
    assert wait_until(lambda: terminal.session is not None and server.shells)
    assert terminal.server == f"{USER}@127.0.0.1:{server.port}"
    assert window.terminal_titles[1].text() == "Terminal (127.0.0.1)"
    box = window.terminal_shell_boxes[1]
    assert box.currentText() == "SSH: Pruebas"  # el perfil de ese servidor
    assert wait_until(lambda: "$" in "".join("".join(cell[0] for cell in terminal.screen_.line(i)) for i in range(terminal.screen_.line_count)))
    terminal._last_activity = 0
    view.navigate(remote.join(view.path, "web"))
    assert wait_until(lambda: f"cd /home/{USER}/web".encode() in server.shells[-1].received)
    terminal._last_activity = 0
    window.run_command("Set SFTP=Off")  # el panel vuelve al equipo: su terminal también
    assert wait_until(lambda: terminal.server == "" and FakeSession.instances)
    assert FakeSession.instances[-1].cwd == str(window.right)


def test_opening_a_remote_file_and_uploading_the_saved_change(window, server, answers, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    with open(server_path(server, f"/home/{USER}/texto.txt"), "w") as handle:
        handle.write("original")
    view = connect_pane(window)
    assert wait_until(lambda: "texto.txt" in {entry.name for entry in view.model.entries})
    entry = next(entry for entry in view.model.entries if entry.name == "texto.txt")
    window._open_file(view, entry)
    assert wait_until(lambda: opened)
    with open(opened[0], encoding="utf-8") as handle:
        assert handle.read() == "original"
    with open(opened[0], "w", encoding="utf-8") as handle:
        handle.write("cambiado en el equipo")
    assert wait_until(lambda: open(server_path(server, f"/home/{USER}/texto.txt")).read() == "cambiado en el equipo", 8)
    assert "texto.txt" in answers["questions"][-1]


def test_saved_changes_upload_without_asking_unless_the_server_changed(window, server, answers, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    window.config[sftp.UPLOAD_KEY] = "auto"
    on_server = server_path(server, f"/home/{USER}/notas.txt")
    with open(on_server, "w") as handle:
        handle.write("original")
    view = connect_pane(window)
    assert wait_until(lambda: "notas.txt" in {entry.name for entry in view.model.entries})
    answers["questions"].clear()  # la de confiar en el servidor, al conectar
    window._open_file(view, next(entry for entry in view.model.entries if entry.name == "notas.txt"))
    assert wait_until(lambda: opened)
    with open(opened[0], "w", encoding="utf-8") as handle:
        handle.write("primer cambio")
    assert wait_until(lambda: open(on_server).read() == "primer cambio", 8)
    assert answers["questions"] == []  # sin preguntar
    time.sleep(1.1)  # otra fecha de modificación en el servidor
    with open(on_server, "w") as handle:
        handle.write("lo cambió otra persona en el servidor")
    with open(opened[0], "w", encoding="utf-8") as handle:
        handle.write("segundo cambio")
    assert wait_until(lambda: answers["questions"], 8)
    assert "ha cambiado" in answers["questions"][-1]  # aquí sí: se perdería ese cambio
    assert wait_until(lambda: open(on_server).read() == "segundo cambio", 8)  # se contestó que sí


def test_opening_can_be_turned_off(window, server, monkeypatch):
    window.config[sftp.OPEN_KEY] = False
    with open(server_path(server, f"/home/{USER}/t.txt"), "w") as handle:
        handle.write("x")
    view = connect_pane(window)
    assert wait_until(lambda: "t.txt" in {entry.name for entry in view.model.entries})
    window._open_file(view, next(entry for entry in view.model.entries if entry.name == "t.txt"))
    assert "desactivado" in window.statusBar().currentMessage()


def test_remote_previews_are_off_until_asked(window, server, monkeypatch):
    with open(server_path(server, f"/home/{USER}/img.txt"), "w") as handle:
        handle.write("contenido")
    view = connect_pane(window)
    assert not view.model.remote_previews
    shown = []
    monkeypatch.setattr(window.preview, "show_entry", lambda entry: shown.append(entry))
    window.viewer_dock.show()
    assert wait_until(lambda: "img.txt" in {entry.name for entry in view.model.entries})
    view.select_paths([remote.join(view.path, "img.txt")])
    window._update_preview()
    QTest.qWait(300)
    assert shown and all(entry is None for entry in shown)  # sin descargar nada
    window.config[sftp.PREVIEW_KEY] = True
    window._preferences_changed()
    assert view.model.remote_previews
    shown.clear()
    window._update_preview()
    assert wait_until(lambda: any(entry is not None for entry in shown))
    copy = next(entry for entry in shown if entry is not None)
    assert not remote.is_remote(copy.path) and open(copy.path).read() == "contenido"


def test_the_preferences_page_keeps_profiles_and_forgets_passwords(window, memory_keyring, server):
    from directory_commander_tux_tux.ui.sftp import ProfileDialog, SftpPage
    remote.store_password(f"{USER}@127.0.0.1:{server.port}", "guardada")
    page = SftpPage(window.config)
    assert page.list.count() == 1
    dialog = ProfileDialog(taken=["Pruebas"])
    dialog.name.setText("pruebas")
    dialog.host.setText("otro.org")
    dialog.user.setText("ana")
    dialog._accept()
    assert dialog.notice.isVisibleTo(dialog) and "Ya hay" in dialog.notice.text()
    dialog.name.setText("Otro")
    dialog._accept()
    assert dialog.result() == dialog.DialogCode.Accepted and dialog.profile().server == "ana@otro.org:22"
    page.list.setCurrentRow(0)
    page.forget()
    page.previews.setChecked(True)
    page.apply_to(window.config)
    assert remote.stored_password(f"{USER}@127.0.0.1:{server.port}") is None
    assert window.config[sftp.PREVIEW_KEY] is True and window.config[sftp.PROFILES_KEY][0]["name"] == "Pruebas"


def test_profiles_and_passwords_do_not_travel_in_exported_settings(window, tmp_path):
    from directory_commander_tux_tux.core import settings_io
    target = tmp_path / "ajustes.json"
    settings_io.export_settings(str(target), dict(window.config.data) if hasattr(window.config, "data") else dict(window.config))
    assert "sftp" not in target.read_text(encoding="utf-8")


def test_a_remote_tab_shows_its_host_and_can_only_be_disconnected(window, server):
    view = connect_pane(window)
    group = window.groups[1]
    index = group.indexOf(view)
    assert group.tabText(index) == f"{USER} (127.0.0.1)" and group.tabToolTip(index).startswith("En el servidor:")
    assert not group.tabIcon(index).isNull()
    tools = view._bar_tools()
    assert not tools["remote"].isEnabled() and not tools["disconnect"].isHidden()
    menu = window.build_sftp_menu(view)
    states = {action.text(): action.isEnabled() for action in menu.actions() if action.text()}
    assert states == {"Pruebas": False, "Conexión nueva…": False, "Desconectar del servidor": True, "Administrar perfiles…": True}
    tools["disconnect"].click()
    QTest.qWait(50)
    assert view.path == str(window.right) and tools["remote"].isEnabled() and tools["disconnect"].isHidden()
    assert group.tabText(group.indexOf(view)) == _tab_title(str(window.right))  # en Windows, con su unidad delante


def test_the_tree_of_a_remote_pane_shows_the_server(window, server):
    os.makedirs(server_path(server, f"/home/{USER}/dentro"))
    view = connect_pane(window)
    window.run_command("Set PANETREE=On")
    tree = window.pane_trees[1]
    assert wait_until(lambda: tree.remote_item is not None and tree.home_item.isHidden())
    assert tree.remote_item.text(0) == f"{USER}@127.0.0.1:{server.port}"
    view.navigate(remote.join(view.path, "dentro"))
    assert wait_until(lambda: tree.current_path() == remote.join(f"sftp://{USER}@127.0.0.1:{server.port}/home/{USER}", "dentro"))
    window.run_command("Set SFTP=Off")
    assert wait_until(lambda: tree.remote_item is None and not tree.home_item.isHidden())


def test_a_server_that_stops_answering_does_not_freeze_the_window(window, server, monkeypatch):
    """Sin respuesta, cada petición se rinde: la conexión se da por perdida, y lo que se hace desde la ventana va en segundo plano."""
    import tests.sftp_server as fake
    os.makedirs(server_path(server, f"/home/{USER}/colgada"))
    monkeypatch.setattr(remote, "OPERATION_TIMEOUT", 1)
    original = fake._Files.list_folder

    def hang(self, path):
        if path.endswith("colgada"):
            time.sleep(3)
        return original(self, path)

    monkeypatch.setattr(fake._Files, "list_folder", hang)
    view = connect_pane(window)
    assert wait_until(lambda: remote.is_connected(remote.server_of(view.path)))
    hung = remote.join(view.path, "colgada")
    view.navigate(hung)
    started = time.monotonic()
    window.run_command('CreateFolder NAME="mientras"')
    QApplication.processEvents()
    assert time.monotonic() - started < 0.5  # la ventana no espera al servidor
    assert wait_until(lambda: "Se ha perdido la conexión" in window.statusBar().currentMessage(), 6)
    with pytest.raises(remote.ConnectionLost):
        remote.list_remote(hung)
    assert wait_until(lambda: os.path.isdir(server_path(server, f"/home/{USER}/colgada/mientras")), 10)  # se reconecta sola


def test_a_terminal_connects_with_its_button_and_disconnects(window, server):
    window.run_command("Set TERMINAL=On")  # en un panel del equipo
    terminal = window.terminals[0]
    assert wait_until(lambda: terminal.session is not None)
    box, button, off = window.terminal_shell_boxes[0], window.terminal_ssh_buttons[0], window.terminal_ssh_off_buttons[0]
    assert box.isEnabled() and all(not box.itemText(row).startswith("SSH") for row in range(box.count()))  # en la lista solo los intérpretes
    assert button.isEnabled() and off.isHidden()
    menu = window.build_ssh_menu(0)
    profile = next(action for action in menu.actions() if action.text() == "Pruebas")
    profile.trigger()
    assert wait_until(lambda: terminal.server == f"{USER}@127.0.0.1:{server.port}" and server.shells)
    assert window.terminal_titles[0].text() == "Terminal (127.0.0.1)"
    assert box.count() == 1 and box.currentText() == "SSH: Pruebas" and not box.isEnabled()  # se ve dónde, pero no se cambia
    assert not button.isEnabled() and not off.isHidden()
    led = window.terminal_leds[0]
    window._led_tick()
    lit = led.lit
    window._led_tick()
    assert not led.isHidden() and led.lit != lit  # el foquito de la conexión, parpadeando
    window.groups[0].current.navigate(str(window.right))  # su panel sigue en el equipo: la terminal se queda en el servidor
    QTest.qWait(200)
    assert terminal.server and window._terminal_session(0)["shell"] == "ssh:Pruebas"
    off.click()
    assert wait_until(lambda: terminal.server == "")
    assert box.isEnabled() and button.isEnabled() and off.isHidden()
    window._led_tick()
    assert led.isHidden()
    assert not remote.is_connected(f"{USER}@127.0.0.1:{server.port}")  # nadie más la usaba


def test_disconnecting_the_terminal_of_a_remote_pane_keeps_the_pane_connected(window, server):
    view = connect_pane(window)
    window.run_command("Set TERMINAL=On")
    terminal = window.terminals[1]
    assert wait_until(lambda: terminal.server != "" and server.shells)
    assert not window.terminal_shell_boxes[1].isEnabled()
    window.disconnect_terminal(1)
    assert wait_until(lambda: terminal.server == "")
    assert remote.is_connected(remote.server_of(view.path))  # el panel la sigue usando
    view.navigate(remote.parent(view.path))
    QTest.qWait(200)
    assert terminal.server == ""  # no vuelve sola al servidor


def test_a_connected_tab_and_its_tree_show_a_blinking_light(window, server):
    view = connect_pane(window)
    window.run_command("Set PANETREE=On")
    group, tree = window.groups[1], window.pane_trees[1]
    assert wait_until(lambda: tree.remote_item is not None)
    led_of = lambda: group.tabBar().tabButton(group.indexOf(view), group.tabBar().ButtonPosition.LeftSide)  # noqa: E731
    window._led_tick()
    first = led_of()
    assert first is not None and first.isVisible() and tree.led_lit is not None
    states = {first.lit}
    window._led_tick()
    states.add(led_of().lit)
    assert states == {True, False} and led_of() is first  # parpadea, sin cambiar de foquito
    assert window.groups[0].tabBar().tabButton(0, window.groups[0].tabBar().ButtonPosition.LeftSide) is None  # la del equipo, ninguno
    remote.connection_for(view.path).close()  # se pierde la conexión
    window._led_tick()
    assert led_of() is None and tree.led_lit is None
    remote.connection_for(view.path).ensure()  # vuelve
    window._led_tick()
    assert led_of() is not None and tree.led_lit is not None


def test_settings_menu_opens_the_profiles(window, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "open_preferences", lambda **kwargs: opened.append(kwargs))
    action = next(action for menu in window.menuBar().findChildren(QMenu) for action in menu.actions() if action.text() == "Perfiles SSH/SFTP…")
    action.trigger()
    assert opened == [{"sftp_page": True}]


def test_the_profile_dialog_tests_the_connection(window, server, answers, memory_keyring):
    from directory_commander_tux_tux.ui.sftp import ProfileDialog
    dialog = ProfileDialog(remote.profile_from(window.config[sftp.PROFILES_KEY][0]), taken=[])
    dialog.show()
    dialog.test()
    assert wait_until(lambda: dialog.test_button.isEnabled() and "✘" in dialog.test_result.text())
    assert "falta la contraseña" in dialog.test_result.text() and len(answers["questions"]) == 1  # se confió en el servidor
    dialog.password.setText("mala")
    dialog.test()
    assert wait_until(lambda: dialog.test_button.isEnabled() and "no acepta" in dialog.test_result.text())
    dialog.password.setText(PASSWORD)
    dialog.test()
    assert wait_until(lambda: "✔" in dialog.test_result.text())
    assert f"/home/{USER}" in dialog.test_result.text()
    assert remote.active_servers() == [] and memory_keyring.stored == {}  # probar no deja nada
    dialog.host.setText("")
    dialog.test()
    assert "Faltan el servidor" in dialog.test_result.text()


def test_leaving_with_connections_asks_and_closes_them(window, server, answers):
    view = connect_pane(window)
    answers["questions"].clear()
    window.config["confirm_exit"] = False
    no = lambda *_args: QMessageBox.StandardButton.No  # noqa: E731
    original = QMessageBox.question
    QMessageBox.question = lambda _parent, _title, text, *args: (answers["questions"].append(text), no())[1]
    try:
        window.run_command("Close")
    finally:
        QMessageBox.question = original
    assert window.isVisible() and remote.is_connected(remote.server_of(view.path))  # se ha preguntado aunque no se pida confirmar
    assert f"{USER}@127.0.0.1:{server.port}" in answers["questions"][-1]
    window.run_command("Close")  # el arreglo de pruebas contesta que sí
    assert not window.isVisible() and remote.active_servers() == []


def test_leaving_asks_only_if_wanted(window, answers):
    window.config["confirm_exit"] = False
    window.run_command("Close")
    assert not window.isVisible() and answers["questions"] == []


def test_leaving_asks_by_default(window, answers):
    answers["questions"].clear()
    window.run_command("Close")
    assert not window.isVisible() and answers["questions"] and "¿Salir" in answers["questions"][-1]


def test_a_password_given_with_the_profile_is_not_asked_again(window, server, answers, memory_keyring):
    from directory_commander_tux_tux.ui.sftp import ProfileDialog, SftpPage
    page = SftpPage(window.config)
    page.list.setCurrentRow(0)
    old = page._profiles[0]
    dialog = ProfileDialog(old, taken=[])
    dialog.password.setText(PASSWORD)
    page._profiles[0] = dialog.profile()
    page._remember_password(dialog.profile(), dialog.typed_password())
    page.apply_to(window.config)
    assert remote.stored_password(old.server) == PASSWORD
    sftp._session_passwords.clear()
    connect_pane(window)
    assert answers["passwords"] == []  # solo se preguntó por la huella del servidor


def test_profiles_travel_in_exported_settings_without_passwords(window, tmp_path, memory_keyring):
    from directory_commander_tux_tux.core import settings_io
    remote.store_password("probador@127.0.0.1:22", "no-debe-salir")
    data = {key: window.config.get(key) for key in settings_io.SETTINGS_TYPES if window.config.get(key) is not None}
    target = tmp_path / "ajustes.json"
    settings_io.export_settings(str(target), data)
    text = target.read_text(encoding="utf-8")
    assert '"ssh_profiles"' in text and "no-debe-salir" not in text and PASSWORD not in text
    settings, _discarded = settings_io.read_settings(str(target))
    assert settings["ssh_profiles"][0]["name"] == "Pruebas"


def test_a_session_with_remote_tabs_reconnects_them(window, server, answers):
    with open(server_path(server, f"/home/{USER}/sigue.txt"), "w") as handle:
        handle.write("x")
    connect_pane(window)
    snapshot = window._session_snapshot()
    assert any(remote.is_remote(path) for tabs in snapshot["tabs"] for path in tabs)
    remote.disconnect_all()
    sftp._session_passwords.clear()
    window.groups[1].close_all_tabs()
    window._apply_session(snapshot, [], live=True)
    restored = next(v for v in window.groups[1].views() if remote.is_remote(v.path))
    assert wait_until(lambda: "sigue.txt" in {entry.name for entry in restored.model.entries}, 10)
    assert len(answers["passwords"]) == 1  # la contraseña ya estaba en el llavero: solo se pidió la primera vez
