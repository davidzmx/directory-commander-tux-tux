"""Un intérprete de comandos conectado a una consola real: ConPTY en Windows y pty en Linux. Sin Qt.

Con simples tuberías los programas interactivos (python, ssh, vim, una barra de progreso) no funcionarían:
creerían que nadie los mira y no mostrarían nada, o no aceptarían las teclas."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from collections.abc import Callable

from .i18n import tr

_READ_SIZE = 16384
_CLOSE_WAIT_SECONDS = 2


def restore_library_path(environ=os.environ, frozen: bool = bool(getattr(sys, "frozen", False))) -> None:
    """El ejecutable empaquetado arranca con su carpeta en la ruta de bibliotecas, y lleva las suyas de GTK o GLib: no debe llegar a los programas que lance,
    como un comparador o la terminal. Se puede quitar una vez en marcha: el sistema solo la lee al arrancar cada proceso."""
    original = environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:
        environ["LD_LIBRARY_PATH"] = original
    elif frozen:
        environ.pop("LD_LIBRARY_PATH", None)


def shell_environment(environ=os.environ, extra: dict[str, str] | None = None) -> dict[str, str]:
    """El entorno del intérprete."""
    env = dict(environ)
    restore_library_path(env, False)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env.update(extra or {})
    return env


class PtySession:
    """Recibe lo que escribe el intérprete en `on_data` y su final en `on_exit`, ambos desde otro hilo."""

    def __init__(self, argv: list[str], cwd: str, cols: int, rows: int, on_data: Callable[[bytes], None], on_exit: Callable[[], None],
                 env: dict[str, str] | None = None) -> None:
        self._console = (_WindowsConsole if sys.platform == "win32" else _UnixConsole)(argv, cwd, cols, rows, shell_environment(extra=env))
        self._on_data = on_data
        self._on_exit = on_exit
        self._outgoing: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._threads = [threading.Thread(target=target, daemon=True) for target in (self._read_loop, self._wait_loop, self._write_loop)]
        for thread in self._threads:
            thread.start()

    def write(self, data: bytes) -> None:
        """Desde su propio hilo: un programa que no lee no debe bloquear la ventana."""
        self._outgoing.put(data)

    def resize(self, cols: int, rows: int) -> None:
        self._console.resize(cols, rows)

    def shell_is_idle(self) -> bool:
        """Falso si hay otro programa ejecutándose: lo que se escriba le llegaría a él y no al intérprete."""
        return self._console.shell_is_idle()

    def current_directory(self) -> str | None:
        """La carpeta que el sistema tiene apuntada para el intérprete. None si no se puede saber."""
        return self._console.current_directory()

    def close(self) -> None:
        """Termina el intérprete y lo que tenga en marcha. Por si el sistema tarda, en otro hilo y sin esperar para siempre."""
        self._on_data = lambda _data: None
        self._on_exit = lambda: None
        self._outgoing.put(None)
        closer = threading.Thread(target=self._console.hang_up, daemon=True)
        closer.start()
        closer.join(_CLOSE_WAIT_SECONDS)

    def _read_loop(self) -> None:
        try:
            while True:
                data = self._console.read(_READ_SIZE)
                if not data:
                    break
                self._on_data(data)
        finally:
            self._console.release_output()
            self._on_exit()

    def _wait_loop(self) -> None:
        self._console.wait()
        self._outgoing.put(None)
        self._console.hang_up()  # sin esto la consola sigue abierta y la lectura no termina nunca

    def _write_loop(self) -> None:
        while (data := self._outgoing.get()) is not None:
            try:
                self._console.write(data)
            except OSError:
                return


if sys.platform == "win32":
    import ctypes
    from ctypes import POINTER, Structure, byref, c_short, c_ubyte, c_void_p, wintypes

    _HRESULT = ctypes.c_long
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _STARTF_USESTDHANDLES = 0x00000100
    _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _INFINITE = 0xFFFFFFFF
    _TH32CS_SNAPPROCESS = 2
    _PROCESS_READ = 0x0410  # consultar y leer su memoria
    _PROCESS_QUERY_LIMITED = 0x1000
    _PEB_PARAMETERS = 0x20
    _PARAMETERS_DIRECTORY = 0x38

    class _Coord(Structure):
        _fields_ = [("X", c_short), ("Y", c_short)]

    class _StartupInfo(Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", POINTER(c_ubyte)), ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class _StartupInfoEx(Structure):
        _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", c_void_p)]

    class _ProcessInformation(Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    class _ProcessEntry(Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * wintypes.MAX_PATH)]

    class _ProcessBasicInformation(Structure):
        _fields_ = [("ExitStatus", c_void_p), ("PebBaseAddress", c_void_p), ("AffinityMask", c_void_p), ("BasePriority", c_void_p),
                    ("UniqueProcessId", c_void_p), ("InheritedFromUniqueProcessId", c_void_p)]

    def _declare(name: str, argtypes: list, restype=wintypes.BOOL):
        function = getattr(_kernel32, name)
        function.argtypes, function.restype = argtypes, restype
        return function

    _CreatePipe = _declare("CreatePipe", [POINTER(wintypes.HANDLE), POINTER(wintypes.HANDLE), c_void_p, wintypes.DWORD])
    _ReadFile = _declare("ReadFile", [wintypes.HANDLE, c_void_p, wintypes.DWORD, POINTER(wintypes.DWORD), c_void_p])
    _WriteFile = _declare("WriteFile", [wintypes.HANDLE, c_void_p, wintypes.DWORD, POINTER(wintypes.DWORD), c_void_p])
    _CloseHandle = _declare("CloseHandle", [wintypes.HANDLE])
    _WaitForSingleObject = _declare("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD)
    _InitializeProcThreadAttributeList = _declare("InitializeProcThreadAttributeList", [c_void_p, wintypes.DWORD, wintypes.DWORD, POINTER(ctypes.c_size_t)])
    _UpdateProcThreadAttribute = _declare("UpdateProcThreadAttribute", [c_void_p, wintypes.DWORD, ctypes.c_size_t, c_void_p, ctypes.c_size_t, c_void_p, c_void_p])
    _DeleteProcThreadAttributeList = _declare("DeleteProcThreadAttributeList", [c_void_p], None)
    _CreateToolhelp32Snapshot = _declare("CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE)
    _Process32FirstW = _declare("Process32FirstW", [wintypes.HANDLE, POINTER(_ProcessEntry)])
    _Process32NextW = _declare("Process32NextW", [wintypes.HANDLE, POINTER(_ProcessEntry)])
    _GetProcessTimes = _declare("GetProcessTimes", [wintypes.HANDLE, POINTER(wintypes.FILETIME), POINTER(wintypes.FILETIME), POINTER(wintypes.FILETIME), POINTER(wintypes.FILETIME)])
    _OpenProcess = _declare("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE)
    _ReadProcessMemory = _declare("ReadProcessMemory", [wintypes.HANDLE, c_void_p, c_void_p, ctypes.c_size_t, POINTER(ctypes.c_size_t)])
    _NtQueryInformationProcess = ctypes.WinDLL("ntdll").NtQueryInformationProcess
    _NtQueryInformationProcess.argtypes = [wintypes.HANDLE, wintypes.ULONG, c_void_p, wintypes.ULONG, POINTER(wintypes.ULONG)]
    _NtQueryInformationProcess.restype = ctypes.c_long
    _CreateProcessW = _declare("CreateProcessW", [wintypes.LPCWSTR, wintypes.LPWSTR, c_void_p, c_void_p, wintypes.BOOL, wintypes.DWORD, c_void_p, wintypes.LPCWSTR,
                                                  POINTER(_StartupInfoEx), POINTER(_ProcessInformation)])

    def _creation_time(process: wintypes.HANDLE) -> int | None:
        created, other = wintypes.FILETIME(), wintypes.FILETIME()
        if not _GetProcessTimes(process, byref(created), byref(other), byref(other), byref(other)):
            return None
        return created.dwHighDateTime << 32 | created.dwLowDateTime

    class _WindowsConsole:
        def __init__(self, argv: list[str], cwd: str, cols: int, rows: int, env: dict[str, str]) -> None:
            try:
                self._create = _declare("CreatePseudoConsole", [_Coord, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD, POINTER(wintypes.HANDLE)], _HRESULT)
                self._resize = _declare("ResizePseudoConsole", [wintypes.HANDLE, _Coord], _HRESULT)
                self._close = _declare("ClosePseudoConsole", [wintypes.HANDLE], None)
            except AttributeError:
                raise OSError(tr("Esta versión de Windows no admite terminales integradas")) from None
            self._lock = threading.Lock()
            self._console = None
            input_read, self._input = self._pipe()
            self._output, output_write = self._pipe()
            console = wintypes.HANDLE()
            result = self._create(_Coord(cols, rows), input_read, output_write, 0, byref(console))
            try:
                if result < 0:
                    raise ctypes.WinError(result & 0xFFFF)
                self._console = console
                self._process = self._spawn(argv, cwd, env)
            except OSError:
                self.hang_up()
                self.release_output()
                raise
            finally:
                # Ahora son de la consola. Si quedaran abiertos aquí, la lectura no vería nunca el final.
                _CloseHandle(input_read)
                _CloseHandle(output_write)

        @staticmethod
        def _pipe() -> tuple[wintypes.HANDLE, wintypes.HANDLE]:
            read, write = wintypes.HANDLE(), wintypes.HANDLE()
            if not _CreatePipe(byref(read), byref(write), None, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            return read, write

        def _spawn(self, argv: list[str], cwd: str, env: dict[str, str]) -> wintypes.HANDLE:
            size = ctypes.c_size_t()
            _InitializeProcThreadAttributeList(None, 1, 0, byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            if not _InitializeProcThreadAttributeList(attributes, 1, 0, byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not _UpdateProcThreadAttribute(attributes, 0, _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, self._console, ctypes.sizeof(wintypes.HANDLE), None, None):
                    raise ctypes.WinError(ctypes.get_last_error())
                startup = _StartupInfoEx()
                startup.StartupInfo.cb = ctypes.sizeof(_StartupInfoEx)
                # Con manejadores no válidos usa los de la consola, aunque este programa tenga la salida redirigida.
                startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
                startup.StartupInfo.hStdInput = startup.StartupInfo.hStdOutput = startup.StartupInfo.hStdError = _INVALID_HANDLE
                startup.lpAttributeList = ctypes.cast(attributes, c_void_p)
                process = _ProcessInformation()
                command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
                block = ctypes.create_unicode_buffer("\0".join(f"{name}={value}" for name, value in sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0")
                if not _CreateProcessW(None, command, None, None, False, _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT, block, cwd, byref(startup), byref(process)):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                _DeleteProcThreadAttributeList(attributes)
            _CloseHandle(process.hThread)
            self._process_id = process.dwProcessId
            self._created = _creation_time(process.hProcess)
            return process.hProcess

        def shell_is_idle(self) -> bool:
            """Lo que el intérprete ejecuta es un proceso hijo suyo. La consola no cuenta: es hija de este programa."""
            if self._console is None:
                return False
            snapshot = _CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
            if snapshot == _INVALID_HANDLE:
                return False
            try:
                entry = _ProcessEntry()
                entry.dwSize = ctypes.sizeof(_ProcessEntry)
                found = _Process32FirstW(snapshot, byref(entry))
                while found:
                    if entry.th32ParentProcessID == self._process_id and self._started_after_shell(entry.th32ProcessID):
                        return False
                    found = _Process32NextW(snapshot, byref(entry))
                return True
            finally:
                _CloseHandle(snapshot)

        def _started_after_shell(self, pid: int) -> bool:
            """Windows reutiliza los números de proceso, y el del padre no se borra al morir este: un programa anterior al intérprete no es hijo suyo,
            aunque lo parezca. Si no se puede comprobar, se da por hijo: esperar es mejor que escribirle una orden a un programa."""
            process = _OpenProcess(_PROCESS_QUERY_LIMITED, False, pid)
            if not process:
                return True
            try:
                created = _creation_time(process)
            finally:
                _CloseHandle(process)
            return created is None or self._created is None or created >= self._created

        def current_directory(self) -> str | None:
            """Windows solo la guarda en la memoria del propio proceso. Estas posiciones son las de los programas de 64 bits, y no han cambiado desde hace décadas."""
            if ctypes.sizeof(c_void_p) != 8:
                return None
            process = _OpenProcess(_PROCESS_READ, False, self._process_id)
            if not process:
                return None
            try:
                basic = _ProcessBasicInformation()
                if _NtQueryInformationProcess(process, 0, byref(basic), ctypes.sizeof(basic), None) < 0 or not basic.PebBaseAddress:
                    return None
                parameters, length, address = c_void_p(), ctypes.c_ushort(), c_void_p()
                if not _ReadProcessMemory(process, basic.PebBaseAddress + _PEB_PARAMETERS, byref(parameters), ctypes.sizeof(parameters), None) or not parameters.value:
                    return None
                directory = parameters.value + _PARAMETERS_DIRECTORY
                if not _ReadProcessMemory(process, directory, byref(length), ctypes.sizeof(length), None):
                    return None
                if not _ReadProcessMemory(process, directory + 8, byref(address), ctypes.sizeof(address), None) or not address.value:
                    return None
                text = ctypes.create_string_buffer(length.value)
                if not _ReadProcessMemory(process, address.value, text, length.value, None):
                    return None
            finally:
                _CloseHandle(process)
            path = text.raw.decode("utf-16-le", "replace").removeprefix("\\\\?\\")
            return path.rstrip("\\") + "\\" if len(path.rstrip("\\")) == 2 else path.rstrip("\\")

        def read(self, size: int) -> bytes:
            buffer = ctypes.create_string_buffer(size)
            done = wintypes.DWORD()
            if not _ReadFile(self._output, buffer, size, byref(done), None):
                return b""
            return buffer.raw[:done.value]

        def write(self, data: bytes) -> None:
            done = wintypes.DWORD()
            while data:
                if not _WriteFile(self._input, data, len(data), byref(done), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                data = data[done.value:]

        def resize(self, cols: int, rows: int) -> None:
            with self._lock:
                if self._console is not None:
                    self._resize(self._console, _Coord(cols, rows))

        def wait(self) -> None:
            _WaitForSingleObject(self._process, _INFINITE)
            _CloseHandle(self._process)

        def hang_up(self) -> None:
            with self._lock:  # llegan a la vez el cierre y el final del proceso: un manejador cerrado dos veces podría ser ya de otro
                console, self._console = self._console, None
                pipe, self._input = self._input, None
            if console is not None:
                self._close(console)  # acaba con lo que se ejecute en ella, y entonces termina la lectura
            if pipe is not None:
                _CloseHandle(pipe)

        def release_output(self) -> None:
            pipe, self._output = self._output, None
            if pipe is not None:
                _CloseHandle(pipe)

else:
    import fcntl
    import select
    import signal
    import struct
    import termios
    import pty

    _POLL_SECONDS = 0.2

    def _take_terminal() -> None:
        """En el proceso hijo, tras crear su sesión: sin esto Ctrl+C no le llegaría."""
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    class _UnixConsole:
        def __init__(self, argv: list[str], cwd: str, cols: int, rows: int, env: dict[str, str]) -> None:
            self._master, follower = pty.openpty()
            self._hung_up = False
            try:
                fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                self._process = subprocess.Popen(argv, stdin=follower, stdout=follower, stderr=follower, cwd=cwd, env=env,
                                                 start_new_session=True, preexec_fn=_take_terminal)
            except OSError:
                os.close(self._master)
                raise
            finally:
                os.close(follower)

        def read(self, size: int) -> bytes:
            while not self._hung_up:
                if select.select([self._master], [], [], _POLL_SECONDS)[0]:
                    try:
                        return os.read(self._master, size)
                    except OSError:  # Linux avisa así de que ya nadie tiene abierta la consola
                        return b""
            return b""

        def write(self, data: bytes) -> None:
            while data:
                data = data[os.write(self._master, data):]

        def resize(self, cols: int, rows: int) -> None:
            if not self._hung_up:
                fcntl.ioctl(self._master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

        def shell_is_idle(self) -> bool:
            if self._hung_up:
                return False
            try:
                return os.tcgetpgrp(self._master) == self._process.pid  # el grupo en primer plano es el que recibe el teclado
            except OSError:
                return False

        def current_directory(self) -> str | None:
            try:
                return os.readlink(f"/proc/{self._process.pid}/cwd")
            except OSError:
                return None

        def wait(self) -> None:
            self._process.wait()

        def hang_up(self) -> None:
            self._hung_up = True
            try:
                os.killpg(self._process.pid, signal.SIGHUP)
            except OSError:
                pass  # ya había terminado

        def release_output(self) -> None:
            os.close(self._master)
