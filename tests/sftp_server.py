"""Un servidor SFTP de pruebas, dentro del propio proceso, sobre una carpeta temporal. También abre una terminal falsa,
que contesta a lo que se le escribe: basta para probar la del panel remoto sin un servidor de verdad."""
from __future__ import annotations

import os
import socket
import threading
from functools import lru_cache

import paramiko

USER, PASSWORD = "probador", "clave-de-prueba"


@lru_cache(maxsize=2)
def host_key(seed: int = 0) -> paramiko.RSAKey:
    """Generar una tarda: se reutiliza entre pruebas. Otra semilla, otra clave, para simular un servidor que ha cambiado."""
    return paramiko.RSAKey.generate(2048)


class _Handle(paramiko.SFTPHandle):
    def stat(self):
        try:
            return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno() if hasattr(self, "readfile") else self.writefile.fileno()))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def chattr(self, attr):
        return paramiko.SFTP_OK


class _Files(paramiko.SFTPServerInterface):
    ROOT = ""

    def _real(self, path: str) -> str:
        return os.path.join(self.ROOT, self.canonicalize(path).lstrip("/"))

    def canonicalize(self, path):
        if not path.startswith("/"):
            path = "/home/" + USER + "/" + path
        return os.path.normpath(path).replace(os.sep, "/").replace("//", "/") or "/"

    def list_folder(self, path):
        real = self._real(path)
        try:
            result = []
            for name in os.listdir(real):
                attrs = paramiko.SFTPAttributes.from_stat(os.lstat(os.path.join(real, name)))
                attrs.filename = name
                result.append(attrs)
            return result
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._real(path)))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def lstat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.lstat(self._real(path)))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def open(self, path, flags, attr):
        real = self._real(path)
        binary = getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(real, flags | binary, 0o644)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        if flags & os.O_WRONLY:
            mode = "ab" if flags & os.O_APPEND else "wb"
        elif flags & os.O_RDWR:
            mode = "a+b" if flags & os.O_APPEND else "r+b"
        else:
            mode = "rb"
        handle = _Handle(flags)
        stream = os.fdopen(fd, mode)
        handle.filename = real
        handle.readfile = stream
        handle.writefile = stream
        return handle

    def remove(self, path):
        try:
            os.remove(self._real(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def rename(self, oldpath, newpath):
        if os.path.exists(self._real(newpath)):
            return paramiko.SFTP_FAILURE  # como SFTP versión 3: no pisa
        try:
            os.rename(self._real(oldpath), self._real(newpath))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def posix_rename(self, oldpath, newpath):
        try:
            os.replace(self._real(oldpath), self._real(newpath))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def mkdir(self, path, attr):
        try:
            os.mkdir(self._real(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def rmdir(self, path):
        try:
            os.rmdir(self._real(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def chattr(self, path, attr):
        try:
            if attr.st_mtime is not None:
                os.utime(self._real(path), (attr.st_atime or attr.st_mtime, attr.st_mtime))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK


class FakeShell:
    """Lo que llega a la terminal remota, y una respuesta sencilla: el indicador y la carpeta tras cada cd."""

    def __init__(self, channel) -> None:
        self.channel = channel
        self.received = b""
        self.folder = "/home/" + USER
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        self.channel.sendall(b"bienvenido\r\n$ ")
        line = b""
        while True:
            data = self.channel.recv(1024)
            if not data:
                break
            self.received += data
            for byte in data:
                char = bytes([byte])
                if char == b"\r":
                    command = line.decode("utf-8", "replace").strip()
                    if command.startswith("cd "):
                        self.folder = command[3:].strip().strip("'")
                    self.channel.sendall(b"\r\n" + f"[{self.folder}]".encode() + b"\r\n$ ")
                    line = b""
                else:
                    line += char
                    self.channel.sendall(char)


class _Server(paramiko.ServerInterface):
    def __init__(self, owner: "SftpServer") -> None:
        self.owner = owner

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == (USER, PASSWORD) else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        self.owner.pty_sizes.append((width, height))
        return True

    def check_channel_window_change_request(self, channel, width, height, pixelwidth, pixelheight):
        self.owner.pty_sizes.append((width, height))
        return True

    def check_channel_shell_request(self, channel):
        self.owner.shells.append(FakeShell(channel))
        return True


class SftpServer:
    """En 127.0.0.1, en un puerto libre. `root` es la carpeta que se ve como raíz del servidor."""

    def __init__(self, root: str, key=None) -> None:
        self.root = root
        self.key = key or host_key()
        os.makedirs(os.path.join(root, "home", USER), exist_ok=True)
        self.shells: list[FakeShell] = []
        self.pty_sizes: list[tuple[int, int]] = []
        self.transports: list[paramiko.Transport] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._running = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        files = type("Files", (_Files,), {"ROOT": self.root})
        while self._running:
            try:
                client, _address = self.sock.accept()
            except OSError:
                break
            transport = paramiko.Transport(client)
            transport.add_server_key(self.key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, files)
            self.transports.append(transport)
            try:
                transport.start_server(server=_Server(self))
            except (paramiko.SSHException, EOFError, OSError):
                continue

    def drop_connections(self) -> None:
        """Como si se cortase la red."""
        for transport in self.transports:
            transport.close()

    def close(self) -> None:
        self._running = False
        self.drop_connections()
        self.sock.close()
