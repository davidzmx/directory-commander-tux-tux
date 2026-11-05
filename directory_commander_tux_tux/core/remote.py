"""Paneles remotos por SFTP: las rutas, los perfiles, las contraseñas, las claves de los servidores y las conexiones. Sin Qt.

Una ruta remota es `sftp://usuario@servidor:puerto/carpeta`. Con ella se sabe siempre a qué conexión pertenece, y ninguna función
de rutas locales la confunde con una carpeta del equipo: por eso no debe pasar nunca por `os.path.normpath`, que en Windows cambiaría sus barras.
"""
from __future__ import annotations

import base64
import os
import posixpath
import shlex
import stat
import threading
from dataclasses import dataclass

from .i18n import tr
from .listing import Entry
from .shells import POSIX, Shell

SCHEME = "sftp://"
DEFAULT_PORT = 22
KEYRING_SERVICE = "directory-commander-tux-tux-sftp"
CONNECT_TIMEOUT = 15
OPERATION_TIMEOUT = 20  # sin respuesta en este tiempo, la conexión se da por perdida: sin límite, un servidor colgado no soltaría nunca
KEEPALIVE_SECONDS = 15
AUTH_KINDS = ("password", "key", "agent")
SSH_SHELL = Shell("ssh", "SSH", [], POSIX)  # la terminal de un panel remoto: un intérprete en el servidor, por la misma conexión
PREVIEW_DEFAULT_MB = 20


# ------------------------------------------------------------------ rutas
def is_remote(path) -> bool:
    return isinstance(path, str) and path.startswith(SCHEME)


def server_key(user: str, host: str, port: int = DEFAULT_PORT) -> str:
    """El nombre de una conexión: también es el principio de sus rutas."""
    shown = f"[{host}]" if ":" in host else host
    return f"{user}@{shown}:{port}"


def _split(path: str) -> tuple[str, str]:
    rest = path[len(SCHEME):]
    slash = rest.find("/")
    server, inner = (rest, "/") if slash < 0 else (rest[:slash], rest[slash:])
    inner = posixpath.normpath(inner)
    if inner.startswith("//"):
        inner = "/" + inner.lstrip("/")  # POSIX deja dos barras al principio: aquí no significan nada
    return server, inner


def server_of(path: str) -> str:
    """La conexión de esa ruta, o "" si es local."""
    return _split(path)[0] if is_remote(path) else ""


def inner_of(path: str) -> str:
    """La carpeta dentro del servidor."""
    return _split(path)[1]


def server_parts(server: str) -> tuple[str, str, int]:
    """(usuario, servidor, puerto) de un nombre de conexión."""
    user, rest = server.split("@", 1)
    host, port = rest.rsplit(":", 1)
    return user, host.strip("[]"), int(port)


def make_path(server: str, inner: str = "/") -> str:
    inner = posixpath.normpath("/" + inner.lstrip("/")) if inner else "/"
    return f"{SCHEME}{server}{'/' + inner.lstrip('/')}"


def normalize(path: str) -> str:
    return make_path(*_split(path))


def join(path: str, name: str) -> str:
    server, inner = _split(path)
    return make_path(server, posixpath.join(inner, name))


def parent(path: str) -> str:
    """"" en la raíz del servidor."""
    server, inner = _split(path)
    return "" if inner == "/" else make_path(server, posixpath.dirname(inner))


def root(path: str) -> str:
    return make_path(server_of(path), "/")


def name_of(path: str) -> str:
    server, inner = _split(path)
    return posixpath.basename(inner) or server


def label(path: str) -> str:
    """Para el título de la ventana: el servidor sin el puerto de siempre, y la carpeta."""
    server, inner = _split(path)
    return f"{server.removesuffix(f':{DEFAULT_PORT}')}:{inner}"


def crumbs(path: str) -> list[tuple[str, str]]:
    """Los tramos de la barra de ruta: el servidor y después cada carpeta."""
    server, inner = _split(path)
    result = [(server.removesuffix(f":{DEFAULT_PORT}"), make_path(server, "/"))]
    current = "/"
    for part in inner.strip("/").split("/") if inner != "/" else ():
        current = posixpath.join(current, part)
        result.append((part, make_path(server, current)))
    return result


def is_inside(child: str, folder: str) -> bool:
    if server_of(child) != server_of(folder):
        return False
    inner, outer = inner_of(child), inner_of(folder)
    return inner == outer or inner.startswith(outer.rstrip("/") + "/")


# ------------------------------------------------------------------ perfiles
@dataclass
class Profile:
    """Lo que hace falta para conectar. La contraseña no va aquí: se guarda en el llavero del sistema."""
    name: str
    host: str
    port: int = DEFAULT_PORT
    user: str = ""
    auth: str = "password"
    key_file: str = ""
    folder: str = ""  # vacío: la carpeta personal en el servidor
    save_password: bool = True

    @property
    def server(self) -> str:
        return server_key(self.user, self.host, self.port)

    def to_dict(self) -> dict:
        return {"name": self.name, "host": self.host, "port": self.port, "user": self.user, "auth": self.auth,
                "key_file": self.key_file, "folder": self.folder, "save_password": self.save_password}


def profile_from(raw) -> Profile | None:
    """Uno válido, o None. Puede venir editado a mano."""
    if not isinstance(raw, dict):
        return None
    name, host, user = (str(raw.get(key) or "").strip() for key in ("name", "host", "user"))
    port = raw.get("port", DEFAULT_PORT)
    if not name or not host or not user or not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        return None
    if any(ch.isspace() or ch in "/@" for ch in host) or any(ch.isspace() or ch in "/@:" for ch in user):
        return None
    auth = raw.get("auth") if raw.get("auth") in AUTH_KINDS else "password"
    folder = str(raw.get("folder") or "").strip()
    return Profile(name, host, port, user, auth, str(raw.get("key_file") or "").strip(),
                   posixpath.normpath(folder) if folder else "", raw.get("save_password") is not False)


def clean_profiles(raw) -> list[dict]:
    """Los válidos, sin nombres repetidos."""
    seen: set[str] = set()
    result = []
    for item in raw if isinstance(raw, list) else []:
        profile = profile_from(item)
        if profile is not None and profile.name.casefold() not in seen:
            seen.add(profile.name.casefold())
            result.append(profile.to_dict())
    return result


def find_profile(raw, name: str) -> Profile | None:
    return next((profile_from(item) for item in clean_profiles(raw) if item["name"].casefold() == name.casefold()), None)


# ------------------------------------------------------------------ contraseñas
def stored_password(server: str) -> str | None:
    """La guardada en el llavero del sistema. None si no hay, o si no hay llavero."""
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, server)
    except Exception:  # sin llavero, o bloqueado: se pregunta
        return None


def store_password(server: str, password: str) -> bool:
    """False si no hay un llavero donde guardarla: entonces solo se recuerda hasta cerrar el programa."""
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, server, password)
        return True
    except Exception:
        return False


def forget_password(server: str) -> None:
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, server)
    except Exception:
        pass


# ------------------------------------------------------------------ claves de los servidores
def known_hosts_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".ssh", "known_hosts")


def fingerprint(key) -> str:
    """Como la muestra ssh: SHA256 en base64, sin el relleno."""
    import hashlib
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def host_entry(host: str, port: int) -> str:
    return host if port == DEFAULT_PORT else f"[{host}]:{port}"


class UnknownHostKey(Exception):
    """El servidor no está en known_hosts: hay que preguntar si se confía en él."""

    def __init__(self, host: str, port: int, key) -> None:
        super().__init__(tr("Servidor desconocido: {host}", host=host_entry(host, port)))
        self.host, self.port, self.key = host, port, key
        self.key_type = key.get_name()
        self.fingerprint = fingerprint(key)


class ConnectionLost(OSError):
    """El servidor ha dejado de responder. Al leer otra carpeta se vuelve a conectar."""

    def __init__(self, server: str) -> None:
        super().__init__(tr("Se ha perdido la conexión con {server}", server=server))
        self.server = server


class AuthenticationNeeded(Exception):
    """Falta la contraseña, o la dada no vale."""

    def __init__(self, wrong: bool) -> None:
        super().__init__(tr("La contraseña no es correcta") if wrong else tr("Hace falta la contraseña"))
        self.wrong = wrong


def accept_host_key(host: str, port: int, key, path: str | None = None) -> None:
    """La añade al final de known_hosts, como haría ssh. No toca lo que ya había."""
    _append_known_hosts([f"{host_entry(host, port)} {key.get_name()} {key.get_base64()}"], path or known_hosts_path())


def _append_known_hosts(lines: list[str], path: str) -> None:
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder, mode=0o700, exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.writelines(line + "\n" for line in lines)
    if new and os.name != "nt":
        os.chmod(path, 0o600)


def _host_keys(path: str):
    import paramiko
    keys = paramiko.HostKeys()
    if os.path.isfile(path):
        try:
            keys.load(path)
        except (OSError, UnicodeError):
            pass
    return keys


def known_host_lines(servers, path: str | None = None) -> list[str]:
    """Las claves que se conocen de estos servidores, dados como (servidor, puerto), en líneas de known_hosts sin cifrar el nombre."""
    keys = _host_keys(path or known_hosts_path())
    lines: list[str] = []
    for host, port in dict.fromkeys(servers):
        entry = host_entry(host, port)
        for kind, key in (keys.lookup(entry) or {}).items():
            lines.append(f"{entry} {kind} {key.get_base64()}")
    return lines


def add_known_hosts(lines, path: str | None = None) -> tuple[int, list[str]]:
    """Añade las que falten. Si ya hay otra clave del mismo tipo para ese servidor, se deja la que había:
    puede que el servidor la haya cambiado, o que el archivo no sea de fiar. Devuelve (añadidas, servidores en conflicto)."""
    from paramiko.hostkeys import HostKeyEntry
    path = path or known_hosts_path()
    keys = _host_keys(path)
    added: list[str] = []
    conflicts: list[str] = []
    for line in lines:
        try:
            entry = HostKeyEntry.from_line(line) if isinstance(line, str) and "\n" not in line else None
        except Exception:  # una clave que no se entiende
            entry = None
        if entry is None or entry.key is None or len(entry.hostnames) != 1 or entry.hostnames[0].startswith("|"):
            continue
        name, kind = entry.hostnames[0], entry.key.get_name()
        known = (keys.lookup(name) or {}).get(kind)
        if known is not None:
            if known.asbytes() != entry.key.asbytes() and name not in conflicts:
                conflicts.append(name)
            continue
        keys.add(name, kind, entry.key)
        added.append(f"{name} {kind} {entry.key.get_base64()}")
    if added:
        _append_known_hosts(added, path)
    return len(added), conflicts


def _reject_unknown():
    import paramiko

    class _Reject(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            host, port = hostname, DEFAULT_PORT
            if hostname.startswith("[") and "]:" in hostname:
                host, port = hostname[1:].split("]:", 1)[0], int(hostname.rsplit(":", 1)[1])
            raise UnknownHostKey(host, port, key)

    return _Reject()


# ------------------------------------------------------------------ conexiones
class Connection:
    """Una sesión SSH con su canal SFTP para listar. Las copias abren su propio canal: así no esperan a que acabe un listado."""

    def __init__(self, profile: Profile, password: str | None = None, known_hosts: str | None = None) -> None:
        self.profile = profile
        self.password = password
        self.known_hosts = known_hosts or known_hosts_path()
        self.lock = threading.RLock()
        self.client = None
        self.sftp = None
        self.home = "/"

    @property
    def server(self) -> str:
        return self.profile.server

    def open(self) -> None:
        import paramiko
        client = paramiko.SSHClient()
        if os.path.exists(self.known_hosts):
            try:
                client.load_host_keys(self.known_hosts)
            except (OSError, ValueError, paramiko.SSHException) as exc:
                raise OSError(tr("No se pudo leer {path}: {exc}", path=self.known_hosts, exc=exc)) from exc
        client.set_missing_host_key_policy(_reject_unknown())  # nunca se añade solo: lo aceptado lo escribe accept_host_key
        profile = self.profile
        key_file = os.path.expanduser(profile.key_file) if profile.auth == "key" and profile.key_file else None
        try:
            client.connect(profile.host, profile.port, profile.user, password=self.password or None, key_filename=key_file,
                           passphrase=self.password or None if key_file else None,
                           allow_agent=profile.auth != "password", look_for_keys=profile.auth != "password",
                           timeout=CONNECT_TIMEOUT, banner_timeout=CONNECT_TIMEOUT, auth_timeout=CONNECT_TIMEOUT)
        except paramiko.BadHostKeyException as exc:
            client.close()
            raise OSError(tr("¡La clave del servidor {host} ha cambiado! Puede que alguien se esté haciendo pasar por él. "
                             "No se conecta. Si sabes por qué ha cambiado, quita la antigua de {path}.",
                             host=host_entry(profile.host, profile.port), path=self.known_hosts)) from exc
        except paramiko.AuthenticationException as exc:
            client.close()
            raise AuthenticationNeeded(bool(self.password)) from exc
        except UnknownHostKey:
            client.close()
            raise
        except (paramiko.SSHException, EOFError) as exc:
            client.close()
            if "authentication methods" in str(exc):  # sin contraseña ni claves que probar
                raise AuthenticationNeeded(False) from exc
            raise OSError(tr("No se pudo conectar con {host}: {exc}", host=profile.host, exc=exc)) from exc
        client.get_transport().set_keepalive(KEEPALIVE_SECONDS)
        try:
            sftp = _with_timeout(client.open_sftp())
            home = sftp.normalize(".")
        except (OSError, paramiko.SSHException) as exc:
            client.close()
            raise OSError(tr("El servidor no ofrece SFTP: {exc}", exc=exc)) from exc
        self.client, self.sftp, self.home = client, sftp, home

    @property
    def alive(self) -> bool:
        transport = self.client.get_transport() if self.client is not None else None
        return transport is not None and transport.is_active()

    def ensure(self) -> None:
        """Si se cortó, se vuelve a conectar con lo mismo: la clave del servidor ya se aceptó."""
        if not self.alive:
            self.close()
            self.open()

    def new_sftp(self):
        """Un canal SFTP propio para una operación larga."""
        with self.lock:
            self.ensure()
            return _with_timeout(self.client.open_sftp())

    def run(self, operation):
        """`operation(sftp)` con el canal de listar. Si el servidor no responde, la conexión se cierra y se avisa: así nadie se queda esperándola."""
        with self.lock:
            self.ensure()
            try:
                return operation(self.sftp)
            except (TimeoutError, EOFError) as exc:
                self.close()
                raise ConnectionLost(self.server) from exc
            except OSError:
                if not self.alive:
                    self.close()
                    raise ConnectionLost(self.server) from None
                raise
            except Exception as exc:  # paramiko avisa de un canal cerrado con su propia excepción
                self.close()
                raise ConnectionLost(self.server) from exc

    def start_path(self) -> str:
        return make_path(self.server, self.profile.folder or self.home)

    def close(self) -> None:
        client, self.client, self.sftp = self.client, None, None
        if client is not None:
            # Con el servidor colgado, cerrar puede esperar: que no lo haga quien la cierra.
            threading.Thread(target=client.close, daemon=True).start()


def _with_timeout(sftp):
    sftp.get_channel().settimeout(OPERATION_TIMEOUT)
    return sftp


_connections: dict[str, Connection] = {}
_connections_lock = threading.Lock()


def connect(profile: Profile, password: str | None = None, known_hosts: str | None = None) -> Connection:
    """Abre una conexión y la deja disponible para sus rutas. Da UnknownHostKey, AuthenticationNeeded u OSError."""
    connection = Connection(profile, password, known_hosts)
    connection.open()
    with _connections_lock:
        previous = _connections.pop(profile.server, None)
        _connections[profile.server] = connection
    if previous is not None:
        previous.close()
    return connection


def connection_for(path: str) -> Connection:
    server = server_of(path)
    with _connections_lock:
        found = _connections.get(server)
    if found is None:
        raise OSError(tr("No hay conexión con {server}: conecta desde su perfil", server=server))
    return found


def is_connected(server: str) -> bool:
    with _connections_lock:
        return server in _connections


def is_alive(server: str) -> bool:
    """Conectada y con la sesión en marcha. Una que se cortó sigue en la lista hasta que se vuelve a conectar o se cierra."""
    with _connections_lock:
        found = _connections.get(server)
    return found is not None and found.alive


def active_servers() -> list[str]:
    with _connections_lock:
        return sorted(_connections)


def probe(profile: Profile, password: str | None = None, known_hosts: str | None = None) -> str:
    """Prueba los datos de un perfil: conecta y se desconecta, sin dejar la conexión abierta. Devuelve la carpeta personal en el servidor.
    Da los mismos errores que `connect`."""
    connection = Connection(profile, password, known_hosts)
    connection.open()
    connection.close()
    return connection.home


def disconnect(server: str) -> None:
    with _connections_lock:
        found = _connections.pop(server, None)
    if found is not None:
        found.close()


def disconnect_all() -> None:
    with _connections_lock:
        found = list(_connections.values())
        _connections.clear()
    for connection in found:
        connection.close()


# ------------------------------------------------------------------ lectura
def _attrs_is_dir(sftp, inner: str, attrs) -> tuple[bool, bool]:
    """(es carpeta, es enlace). Un enlace se sigue para saber si lleva a una carpeta."""
    mode = attrs.st_mode or 0
    if stat.S_ISLNK(mode):
        try:
            return stat.S_ISDIR(sftp.stat(inner).st_mode or 0), True
        except OSError:
            return False, True
    return stat.S_ISDIR(mode), False


def _entry(sftp, folder: str, attrs) -> Entry:
    path = join(folder, attrs.filename)
    is_dir, is_link = _attrs_is_dir(sftp, inner_of(path), attrs)
    return Entry(attrs.filename, path, is_dir, 0 if is_dir else attrs.st_size or 0, float(attrs.st_mtime or 0), is_link,
                 stat.filemode(attrs.st_mode or 0)[1:], attrs.filename.startswith("."))


def list_remote(path: str, show_hidden: bool = False) -> list[Entry]:
    entries = connection_for(path).run(lambda sftp: [_entry(sftp, path, attrs) for attrs in sftp.listdir_attr(inner_of(path))])
    return [entry for entry in entries if show_hidden or not entry.hidden]


def stat_entry(path: str) -> Entry:
    def read(sftp) -> Entry:
        attrs = sftp.lstat(inner_of(path))
        attrs.filename = name_of(path)
        return _entry(sftp, parent(path) or root(path), attrs)

    return connection_for(path).run(read)


def exists(path: str) -> bool:
    try:
        stat_entry(path)
        return True
    except OSError:
        return False


def read_bytes(path: str, limit: int) -> bytes:
    """El principio de un archivo, para una miniatura."""
    connection = connection_for(path)
    sftp = connection.new_sftp()
    try:
        with sftp.open(inner_of(path), "rb") as handle:
            return handle.read(limit)
    finally:
        sftp.close()


def download(path: str, local: str, progress=None) -> None:
    """Lo descarga entero en `local`. `progress(hechos, total)` puede lanzar una excepción para cancelar."""
    connection = connection_for(path)
    sftp = connection.new_sftp()
    try:
        sftp.get(inner_of(path), local, callback=progress)
    finally:
        sftp.close()


def upload(local: str, path: str) -> None:
    """Sube un archivo y sustituye al que haya. Se escribe aparte y se cambia de nombre al final: si se corta, el anterior sigue."""
    connection = connection_for(path)
    sftp = connection.new_sftp()
    inner = inner_of(path)
    partial = inner + ".detuxpart"
    try:
        sftp.put(local, partial)
        _replace(sftp, partial, inner)
    except BaseException:
        try:
            sftp.remove(partial)
        except OSError:
            pass
        raise
    finally:
        sftp.close()


def _replace(sftp, source: str, target: str) -> None:
    """SFTP no deja renombrar encima de otro archivo; OpenSSH sí, con su extensión."""
    try:
        sftp.posix_rename(source, target)
    except OSError:
        try:
            sftp.remove(target)
        except OSError:
            pass
        sftp.rename(source, target)


# ------------------------------------------------------------------ la terminal
def cd_line(path: str) -> str | None:
    """Lo que se escribe para ir a esa carpeta. Con un espacio delante, bash y zsh no lo guardan en el historial si así se configura.
    None si el nombre no se puede escribir sin riesgo."""
    inner = inner_of(path)
    return f" cd {shlex.quote(inner)}\r" if inner.isprintable() else None


class RemoteShellSession:
    """Un intérprete en el servidor, por la misma conexión del panel. Ofrece lo mismo que PtySession a la terminal.
    Se abre en segundo plano: con el servidor lento o colgado, la ventana no espera. Lo escrito mientras tanto se envía al abrirse."""

    def __init__(self, argv, cwd: str, cols: int, rows: int, on_data, on_exit, env=None) -> None:
        self.connection = connection_for(cwd)  # sin conexión, se avisa ya: la terminal lo muestra
        self.channel = None
        self._on_data, self._on_exit = on_data, on_exit
        self._closed = False
        self._size = (cols, rows)
        self._pending: list[bytes] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._run, args=(cwd,), daemon=True).start()

    def _open(self, cwd: str):
        connection = self.connection
        with connection.lock:
            connection.ensure()
            channel = connection.client.get_transport().open_session(timeout=OPERATION_TIMEOUT)
        cols, rows = self._size
        channel.get_pty(term="xterm-256color", width=cols, height=rows)
        channel.invoke_shell()
        line = cd_line(cwd)
        if line and inner_of(cwd) != connection.home:
            channel.sendall(line.encode())
        return channel

    def _run(self, cwd: str) -> None:
        try:
            channel = self._open(cwd)
        except Exception as exc:
            if not self._closed:
                self._on_data(("\r\n" + tr("El servidor no abrió una terminal: {exc}", exc=exc) + "\r\n").encode())
            return
        with self._lock:
            self.channel = channel
            pending, self._pending = self._pending, []
        if self._closed:
            channel.close()
            return
        for data in pending:
            self.write(data)
        try:
            while True:
                data = channel.recv(32768)
                if not data:
                    break
                self._on_data(data)
        except Exception:
            pass
        if not self._closed:
            self._on_exit()

    def write(self, data: bytes) -> None:
        with self._lock:
            if self.channel is None:
                self._pending.append(data)
                return
        try:
            self.channel.sendall(data)
        except Exception:
            pass  # la conexión se ha cortado: lo dirá el hilo de lectura

    def resize(self, cols: int, rows: int) -> None:
        self._size = (cols, rows)
        try:
            if self.channel is not None:
                self.channel.resize_pty(width=cols, height=rows)
        except Exception:
            pass

    def shell_is_idle(self) -> bool:
        return True  # desde aquí no se ve si hay un programa en marcha: manda lo que muestre el indicador

    def current_directory(self):
        return None

    def close(self) -> None:
        self._closed = True
        if self.channel is not None:
            self.channel.close()
