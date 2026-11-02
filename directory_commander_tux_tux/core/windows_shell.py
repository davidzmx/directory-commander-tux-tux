"""Menú contextual de Windows: las opciones del sistema y de otros programas instalados.

Se habla con el Explorador mediante sus interfaces COM, con ctypes para no añadir dependencias. Los programas
se ejecutan dentro de este proceso, igual que en cualquier explorador de archivos, y solo actúan al pedir
un comando con `invoke`: leer el menú no ejecuta ninguna opción.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import POINTER, Structure, byref, c_char_p, c_int, c_uint, c_ulong, c_void_p, c_wchar_p, cast, sizeof
from dataclasses import dataclass

from .i18n import tr

AVAILABLE = sys.platform == "win32"

FIRST_COMMAND = 1
LAST_COMMAND = 0x7FFF
# Lo que el programa ya ofrece a su manera, para no mostrarlo dos veces.
SPECIAL_BITMAPS = set(range(0, 12))
OWN_VERBS = {"cut", "copy", "paste", "pastelink", "delete", "rename", "properties", "refresh", "undo", "redo", "view",
             "arrange", "groupby", "link", "newfolder", "open"}
OPEN_WITH_VERB = "openas"


@dataclass
class ShellItem:
    label: str = ""
    separator: bool = False
    enabled: bool = True
    checked: bool = False
    command: int = 0
    verb: str = ""
    bitmap: int = 0
    submenu: int = 0  # identificador del menú de Windows; sus elementos se leen al abrirlo
    position: int = 0


def shell_key(item: ShellItem) -> str:
    """Identifica una opción. El verbo no cambia con el archivo; el texto sí puede (`Añadir a "notas.rar"`)."""
    return item.verb.lower() if item.verb else item.label.replace("&", "").casefold()


def is_special_bitmap(handle: int) -> bool:
    """Valores reservados de Windows: indican una imagen que dibuja el propio menú."""
    return handle in SPECIAL_BITMAPS or handle >= 2 ** 64 - 16


def choose_bitmap(item_bitmap: int, unchecked_bitmap: int) -> int:
    """La imagen de una opción. Hay programas que la declaran en el campo moderno y otros, como imagen
    de «sin marcar», en el antiguo."""
    return item_bitmap if item_bitmap and not is_special_bitmap(item_bitmap) else unchecked_bitmap


def clean_items(items: list[ShellItem], own_verbs=OWN_VERBS, hidden_keys=()) -> list[ShellItem]:
    """Quita lo repetido, lo que no se puede mostrar y lo que se eligió ocultar, y deja los separadores solo entre grupos."""
    kept: list[ShellItem] = []
    for item in items:
        if item.separator:
            if kept and not kept[-1].separator:
                kept.append(item)
        elif item.label and item.verb.lower() not in own_verbs and shell_key(item) not in hidden_keys:
            kept.append(item)
    while kept and kept[-1].separator:
        kept.pop()
    return kept


if AVAILABLE:
    from ctypes import WINFUNCTYPE, wintypes  # WINFUNCTYPE solo existe en Windows

    class _GUID(Structure):
        _fields_ = [("Data1", c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def _guid(text: str) -> _GUID:
        guid = _GUID()
        ctypes.oledll.ole32.CLSIDFromString(c_wchar_p(text), byref(guid))
        return guid

    IID_ISHELLFOLDER = "{000214E6-0000-0000-C000-000000000046}"
    IID_ICONTEXTMENU = "{000214E4-0000-0000-C000-000000000046}"
    IID_ICONTEXTMENU2 = "{000214F4-0000-0000-C000-000000000046}"
    IID_ICONTEXTMENU3 = "{BCFCE0A0-EC17-11D0-8D10-00A0C90F2719}"
    IID_ISHELLEXTINIT = "{000214E8-0000-0000-C000-000000000046}"
    IID_IDATAOBJECT = "{0000010E-0000-0000-C000-000000000046}"
    CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
    IID_ISHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
    IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"
    CLSCTX_INPROC_SERVER = 0x1
    DROP_HANDLER_KEYS = ("Directory", "Folder")
    DRIVE_DROP_HANDLER_KEY = "Drive"

    CMF_NORMAL = 0x0
    CMF_EXPLORE = 0x4
    CMF_CANRENAME = 0x10
    GCS_VERBW = 4
    MIIM_STATE, MIIM_ID, MIIM_SUBMENU, MIIM_CHECKMARKS, MIIM_STRING, MIIM_BITMAP, MIIM_FTYPE = 0x1, 0x2, 0x4, 0x8, 0x40, 0x80, 0x100
    MFT_SEPARATOR, MFT_OWNERDRAW = 0x800, 0x100
    MFS_DISABLED, MFS_CHECKED = 0x3, 0x8
    WM_INITMENUPOPUP = 0x117
    SW_SHOWNORMAL = 1
    CMIC_MASK_UNICODE = 0x4000
    CMIC_MASK_PTINVOKE = 0x20000000
    COINIT_APARTMENTTHREADED = 0x2
    OAIF_ALLOW_REGISTRATION = 0x1
    OAIF_EXEC = 0x4
    ERROR_CANCELLED = 0x800704C7

    class _MENUITEMINFOW(Structure):
        _fields_ = [("cbSize", c_uint), ("fMask", c_uint), ("fType", c_uint), ("fState", c_uint), ("wID", c_uint),
                    ("hSubMenu", c_void_p), ("hbmpChecked", c_void_p), ("hbmpUnchecked", c_void_p), ("dwItemData", ctypes.c_size_t),
                    ("dwTypeData", c_wchar_p), ("cch", c_uint), ("hbmpItem", c_void_p)]

    class _CMINVOKECOMMANDINFOEX(Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.DWORD), ("hwnd", c_void_p), ("lpVerb", c_void_p),
                    ("lpParameters", c_char_p), ("lpDirectory", c_char_p), ("nShow", c_int), ("dwHotKey", wintypes.DWORD),
                    ("hIcon", c_void_p), ("lpTitle", c_char_p), ("lpVerbW", c_void_p), ("lpParametersW", c_wchar_p),
                    ("lpDirectoryW", c_wchar_p), ("lpTitleW", c_wchar_p), ("ptInvoke", wintypes.POINT)]

    class _OPENASINFO(Structure):
        _fields_ = [("pcszFile", c_wchar_p), ("pcszClass", c_wchar_p), ("oaifInFlags", c_int)]

    class _BITMAP(Structure):
        _fields_ = [("bmType", c_int), ("bmWidth", c_int), ("bmHeight", c_int), ("bmWidthBytes", c_int), ("bmPlanes", ctypes.c_ushort),
                    ("bmBitsPixel", ctypes.c_ushort), ("bmBits", c_void_p)]

    class _BITMAPINFOHEADER(Structure):
        _fields_ = [("biSize", c_uint), ("biWidth", c_int), ("biHeight", c_int), ("biPlanes", ctypes.c_ushort), ("biBitCount", ctypes.c_ushort),
                    ("biCompression", c_uint), ("biSizeImage", c_uint), ("biXPelsPerMeter", c_int), ("biYPelsPerMeter", c_int),
                    ("biClrUsed", c_uint), ("biClrImportant", c_uint)]

    _gdi32 = ctypes.windll.gdi32
    _gdi32.GetObjectW.argtypes = [c_void_p, c_int, c_void_p]
    _gdi32.CreateCompatibleDC.argtypes = [c_void_p]
    _gdi32.CreateCompatibleDC.restype = c_void_p
    _gdi32.DeleteDC.argtypes = [c_void_p]
    _gdi32.GetDIBits.argtypes = [c_void_p, c_void_p, c_uint, c_uint, c_void_p, c_void_p, c_uint]
    _shell32 = ctypes.windll.shell32
    _user32 = ctypes.windll.user32
    _ole32 = ctypes.windll.ole32
    _user32.CreatePopupMenu.restype = c_void_p
    _user32.DestroyMenu.argtypes = [c_void_p]
    _user32.GetMenuItemCount.argtypes = [c_void_p]
    _user32.GetMenuItemInfoW.argtypes = [c_void_p, c_uint, wintypes.BOOL, c_void_p]
    _shell32.SHParseDisplayName.argtypes = [c_wchar_p, c_void_p, POINTER(c_void_p), c_ulong, POINTER(c_ulong)]
    _shell32.SHBindToParent.argtypes = [c_void_p, c_void_p, POINTER(c_void_p), POINTER(c_void_p)]
    _shell32.SHBindToObject.argtypes = [c_void_p, c_void_p, c_void_p, c_void_p, POINTER(c_void_p)]
    _shell32.SHOpenWithDialog.argtypes = [c_void_p, c_void_p]
    _ole32.CoTaskMemFree.argtypes = [c_void_p]
    _ole32.CoInitializeEx.argtypes = [c_void_p, c_ulong]
    _ole32.CoCreateInstance.argtypes = [c_void_p, c_void_p, c_ulong, c_void_p, POINTER(c_void_p)]

    def _method(interface, index: int, restype, *argtypes):
        """Función de la tabla de métodos de una interfaz COM."""
        table = cast(cast(interface, POINTER(c_void_p)).contents, POINTER(c_void_p))
        return WINFUNCTYPE(restype, c_void_p, *argtypes)(table[index])

    def _release(interface) -> None:
        if interface:
            _method(interface, 2, c_ulong)(interface)

    def _query_interface(interface, iid: str):
        result = c_void_p()
        guid = _guid(iid)
        if _method(interface, 0, c_int, c_void_p, POINTER(c_void_p))(interface, byref(guid), byref(result)) < 0:
            return None
        return result

    def _succeeded(hresult: int) -> bool:
        return hresult >= 0

    def _parse_path(path: str):
        pidl = c_void_p()
        if not _succeeded(_shell32.SHParseDisplayName(os.path.normpath(path), None, byref(pidl), 0, None)):
            raise OSError(f"Windows no reconoce la ruta {path}")
        return pidl


class ShellMenu:
    """Un menú de Windows ya preparado. Hay que cerrarlo con `close` cuando deja de mostrarse."""

    def __init__(self, interface, hmenu, window: int, directory: str) -> None:
        self._interface = interface
        self._hmenu = hmenu
        self._window = window
        self._directory = directory
        self._handler2 = _query_interface(interface, IID_ICONTEXTMENU2)
        self._handler3 = _query_interface(interface, IID_ICONTEXTMENU3)

    @property
    def is_open(self) -> bool:
        return self._hmenu is not None

    def items(self) -> list[ShellItem]:
        return self.read_menu(self._hmenu)

    def _verb(self, command: int) -> str:
        offset = command - FIRST_COMMAND
        if offset < 0:
            return ""
        buffer = ctypes.create_unicode_buffer(128)
        function = _method(self._interface, 5, c_int, ctypes.c_size_t, c_uint, c_void_p, c_void_p, c_uint)
        try:  # hay extensiones que fallan aquí con errores de memoria; ctypes los convierte en OSError
            result = function(self._interface, offset, GCS_VERBW, None, buffer, 128)
        except OSError:
            return ""
        return buffer.value if _succeeded(result) else ""

    def read_menu(self, hmenu) -> list[ShellItem]:
        items: list[ShellItem] = []
        for position in range(max(0, _user32.GetMenuItemCount(hmenu))):
            info = _MENUITEMINFOW()
            info.cbSize = sizeof(_MENUITEMINFOW)
            info.fMask = MIIM_FTYPE | MIIM_STATE | MIIM_ID | MIIM_SUBMENU | MIIM_CHECKMARKS | MIIM_BITMAP | MIIM_STRING
            buffer = ctypes.create_unicode_buffer(512)
            info.dwTypeData = cast(buffer, c_wchar_p)
            info.cch = 512
            if not _user32.GetMenuItemInfoW(hmenu, position, True, byref(info)):
                continue
            if info.fType & MFT_SEPARATOR:
                items.append(ShellItem(separator=True))
                continue
            label = "" if info.fType & MFT_OWNERDRAW else buffer.value  # lo dibuja el programa: no hay texto que leer
            verb = self._verb(info.wID)
            if info.hSubMenu and verb.lower() != OPEN_WITH_VERB:
                verb = ""  # de los submenús solo importa «Abrir con», que se coloca aparte
            items.append(ShellItem(label=label, enabled=not info.fState & MFS_DISABLED, checked=bool(info.fState & MFS_CHECKED),
                                   command=info.wID, verb=verb,
                                   bitmap=choose_bitmap(info.hbmpItem or 0, info.hbmpUnchecked or 0),
                                   submenu=info.hSubMenu or 0, position=position))
        return items

    def prepare_submenu(self, hsubmenu: int, position: int = 0) -> None:
        """Muchos programas no llenan sus submenús hasta el momento de abrirlos."""
        function = None
        if self._handler3 is not None:
            function = _method(self._handler3, 7, c_int, c_uint, ctypes.c_size_t, ctypes.c_ssize_t, c_void_p)
            arguments = (self._handler3, WM_INITMENUPOPUP, hsubmenu, position, None)
        elif self._handler2 is not None:
            function = _method(self._handler2, 6, c_int, c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
            arguments = (self._handler2, WM_INITMENUPOPUP, hsubmenu, position)
        if function is not None:
            try:
                function(*arguments)
            except OSError:
                pass  # el submenú quedará como esté

    def invoke(self, command: int) -> bool:
        info = _CMINVOKECOMMANDINFOEX()
        info.cbSize = sizeof(_CMINVOKECOMMANDINFOEX)
        info.fMask = CMIC_MASK_UNICODE
        info.hwnd = self._window
        info.lpVerb = info.lpVerbW = command - FIRST_COMMAND
        info.nShow = SW_SHOWNORMAL
        info.lpDirectoryW = self._directory
        function = _method(self._interface, 4, c_int, c_void_p)
        return _succeeded(function(self._interface, byref(info)))

    def close(self) -> None:
        if self._hmenu is None:
            return
        _user32.DestroyMenu(self._hmenu)
        self._hmenu = None
        for handler in (self._handler2, self._handler3, self._interface):
            _release(handler)
        self._handler2 = self._handler3 = self._interface = None


def read_bitmap(handle: int) -> tuple[int, int, bytes, bool] | None:
    """(ancho, alto, píxeles BGRA de arriba abajo, si tiene transparencia) de la imagen de una opción."""
    if not AVAILABLE or is_special_bitmap(handle):
        return None
    size = _BITMAP()
    if not _gdi32.GetObjectW(handle, sizeof(_BITMAP), byref(size)) or size.bmWidth <= 0 or size.bmHeight <= 0:
        return None
    width, height = size.bmWidth, size.bmHeight
    info = _BITMAPINFOHEADER(sizeof(_BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = ctypes.create_string_buffer(width * height * 4)
    context = _gdi32.CreateCompatibleDC(None)
    try:
        if not _gdi32.GetDIBits(context, handle, 0, height, pixels, byref(info), 0):
            return None
    finally:
        _gdi32.DeleteDC(context)
    raw = pixels.raw
    return width, height, raw, any(raw[3::4])


def _prepare(interface, window: int, directory: str, flags: int) -> ShellMenu:
    hmenu = _user32.CreatePopupMenu()
    function = _method(interface, 3, c_int, c_void_p, c_uint, c_uint, c_uint, c_uint)
    result = function(interface, hmenu, 0, FIRST_COMMAND, LAST_COMMAND, flags)
    if not _succeeded(result):
        _user32.DestroyMenu(hmenu)
        _release(interface)
        raise OSError(tr("Windows no pudo preparar el menú (0x{code:08X})", code=result & 0xFFFFFFFF))
    return ShellMenu(interface, hmenu, window, directory)


def _start_com() -> None:
    _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)  # si ya estaba iniciado, no pasa nada


def _same_folder(paths: list[str]) -> bool:
    return bool(paths) and len({os.path.dirname(os.path.normpath(p)).casefold() for p in paths}) == 1


def menu_for_items(paths: list[str], window: int = 0) -> ShellMenu | None:
    """Menú de unos archivos o carpetas. Windows solo lo prepara si están todos en la misma carpeta."""
    if not AVAILABLE or not _same_folder(paths):
        return None
    interface = _ui_object(paths, window, IID_ICONTEXTMENU)
    return _prepare(interface, window, os.path.dirname(os.path.normpath(paths[0])), CMF_NORMAL | CMF_EXPLORE | CMF_CANRENAME)


def _ui_object(paths: list[str], window: int, iid: str):
    """El objeto de Windows que representa esos elementos, todos de la misma carpeta."""
    _start_com()
    pidls = [_parse_path(path) for path in paths]
    parent = c_void_p()
    try:
        children = (c_void_p * len(pidls))()
        guid = _guid(IID_ISHELLFOLDER)
        for index, pidl in enumerate(pidls):
            last = c_void_p()
            target = byref(parent) if index == 0 else byref(c_void_p())
            if not _succeeded(_shell32.SHBindToParent(pidl, byref(guid), target, byref(last))):
                raise OSError(tr("Windows no pudo abrir la carpeta"))
            children[index] = last
            if index:
                _release(target._obj)
        interface = c_void_p()
        wanted = _guid(iid)
        function = _method(parent, 10, c_int, c_void_p, c_uint, c_void_p, c_void_p, c_void_p, POINTER(c_void_p))
        if not _succeeded(function(parent, window or None, len(pidls), children, byref(wanted), None, byref(interface))):
            raise OSError(tr("Windows no pudo preparar el menú de estos elementos"))
        return interface
    finally:
        _release(parent)
        for pidl in pidls:
            _ole32.CoTaskMemFree(pidl)


def registered_drop_handlers(to_drive: bool) -> list[str]:
    """Los identificadores de las extensiones que añaden opciones al soltar con el botón derecho (TeraCopy, WinRAR…)."""
    import winreg

    found: list[str] = []
    for key in (*DROP_HANDLER_KEYS, *((DRIVE_DROP_HANDLER_KEY,) if to_drive else ())):
        try:
            handlers = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key + r"\shellex\DragDropHandlers")
        except OSError:
            continue
        with handlers:
            for index in range(winreg.QueryInfoKey(handlers)[0]):
                name = winreg.EnumKey(handlers, index)
                clsid = winreg.QueryValue(handlers, name) or name
                if clsid.startswith("{") and clsid.casefold() not in {item.casefold() for item in found}:
                    found.append(clsid)
    return found


def _drop_handler_menu(clsid: str, folder, data):
    """El menú de una extensión de soltar, ya informada de lo que se suelta y dónde. None si no se puede cargar."""
    init = c_void_p()
    class_id, wanted = _guid(clsid), _guid(IID_ISHELLEXTINIT)
    try:
        # Lo que no es de 64 bits, como la versión de 32 bits que registran algunos programas, falla aquí.
        if not _succeeded(_ole32.CoCreateInstance(byref(class_id), None, CLSCTX_INPROC_SERVER, byref(wanted), byref(init))):
            return None
        if not _succeeded(_method(init, 3, c_int, c_void_p, c_void_p, c_void_p)(init, folder, data, None)):
            return None
        return _query_interface(init, IID_ICONTEXTMENU)
    except OSError:
        return None
    finally:
        _release(init)


def drop_handlers(paths: list[str], dest_dir: str, window: int = 0) -> list[ShellMenu]:
    """Las opciones de otros programas al soltar en `dest_dir`. Solo si lo que se suelta viene de una misma carpeta."""
    if not AVAILABLE or not _same_folder(paths):
        return []
    data = _ui_object(paths, window, IID_IDATAOBJECT)
    folder = _parse_path(dest_dir)
    menus: list[ShellMenu] = []
    try:
        for clsid in registered_drop_handlers(os.path.dirname(os.path.abspath(dest_dir)) == os.path.abspath(dest_dir)):
            interface = _drop_handler_menu(clsid, folder, data)
            if interface is not None:
                try:
                    menus.append(_prepare(interface, window, os.path.normpath(dest_dir), CMF_NORMAL))
                except OSError:
                    continue  # esa extensión no quiere saber nada de esto: las demás siguen
    finally:
        _release(data)
        _ole32.CoTaskMemFree(folder)
    return menus


def create_shortcut(target: str, link_path: str) -> None:
    """Crea un acceso directo `.lnk` de Windows."""
    _start_com()
    link = c_void_p()
    class_id, wanted = _guid(CLSID_SHELLLINK), _guid(IID_ISHELLLINKW)
    if not _succeeded(_ole32.CoCreateInstance(byref(class_id), None, CLSCTX_INPROC_SERVER, byref(wanted), byref(link))):
        raise OSError(tr("Windows no pudo crear el acceso directo"))
    persist = None
    try:
        path = os.path.normpath(target)
        _method(link, 20, c_int, c_wchar_p)(link, path)  # SetPath
        _method(link, 9, c_int, c_wchar_p)(link, os.path.dirname(path))  # SetWorkingDirectory
        persist = _query_interface(link, IID_IPERSISTFILE)
        if persist is None or not _succeeded(_method(persist, 6, c_int, c_wchar_p, c_int)(persist, os.path.normpath(link_path), True)):
            raise OSError(tr("Windows no pudo crear el acceso directo"))
    finally:
        _release(persist)
        _release(link)


def open_with_dialog(path: str, window: int = 0) -> None:
    """La ventana de Windows para elegir con qué programa abrir un archivo."""
    _start_com()
    info = _OPENASINFO(os.path.normpath(path), None, OAIF_ALLOW_REGISTRATION | OAIF_EXEC)
    result = _shell32.SHOpenWithDialog(window or None, byref(info))
    if not _succeeded(result) and result & 0xFFFFFFFF != ERROR_CANCELLED:
        raise OSError(tr("Windows no pudo mostrar la ventana «Abrir con» (0x{code:08X})", code=result & 0xFFFFFFFF))


def menu_for_folder(path: str, window: int = 0) -> ShellMenu | None:
    """Menú de una carpeta abierta: el que aparece al pulsar en un hueco de la lista."""
    if not AVAILABLE:
        return None
    _start_com()
    pidl = _parse_path(path)
    folder = c_void_p()
    try:
        guid = _guid(IID_ISHELLFOLDER)
        if not _succeeded(_shell32.SHBindToObject(None, pidl, None, byref(guid), byref(folder))):
            raise OSError(tr("Windows no pudo abrir la carpeta"))
        interface = c_void_p()
        menu_guid = _guid(IID_ICONTEXTMENU)
        function = _method(folder, 8, c_int, c_void_p, c_void_p, POINTER(c_void_p))
        if not _succeeded(function(folder, window or None, byref(menu_guid), byref(interface))):
            raise OSError(tr("Windows no pudo preparar el menú de la carpeta"))
        return _prepare(interface, window, os.path.normpath(path), CMF_NORMAL | CMF_EXPLORE)
    finally:
        _release(folder)
        _ole32.CoTaskMemFree(pidl)
