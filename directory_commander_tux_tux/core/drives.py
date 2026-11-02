"""Unidades de Windows: nombre, tipo (SSD o HDD), espacio y sistema de archivos."""
from __future__ import annotations

import ctypes
import os
import shutil
from ctypes import wintypes

from .listing import Entry
from .i18n import tr

_DRIVE_REMOVABLE, _DRIVE_FIXED, _DRIVE_REMOTE, _DRIVE_CDROM = 2, 3, 4, 5
_IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
_SEEK_PENALTY_PROPERTY = 7
_FILE_SHARE_READ_WRITE = 3
_OPEN_EXISTING = 3
_INVALID_HANDLE = wintypes.HANDLE(-1).value
_SEM_FAILCRITICALERRORS = 1

# Clave de grupo: (título del grupo, tipo que se muestra). El orden es el de la lista de unidades.
DRIVE_GROUPS = {
    "ssd": (tr("Unidades de Estado Sólido"), tr("Unidad de Disco Local (SSD)")),
    "hdd": (tr("Unidades de Disco Duro"), tr("Unidad de Disco Local (HDD)")),
    "local": (tr("Otras Unidades Locales"), tr("Unidad de Disco Local")),
    "removable": (tr("Unidades Extraíbles"), tr("Unidad Extraíble")),
    "optical": (tr("Unidades Ópticas"), tr("Unidad Óptica")),
    "network": (tr("Unidades de Red"), tr("Unidad de Red")),
}


class _SeekPenaltyQuery(ctypes.Structure):
    _fields_ = [("PropertyId", wintypes.DWORD), ("QueryType", wintypes.DWORD), ("AdditionalParameters", ctypes.c_ubyte * 1)]


class _SeekPenaltyAnswer(ctypes.Structure):
    _fields_ = [("Version", wintypes.DWORD), ("Size", wintypes.DWORD), ("IncursSeekPenalty", ctypes.c_ubyte)]


def _kernel32():
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.CreateFileW.restype = wintypes.HANDLE
    library.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                    wintypes.DWORD, wintypes.HANDLE)
    library.DeviceIoControl.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                                        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
    library.CloseHandle.argtypes = (wintypes.HANDLE,)
    return library


def _has_seek_penalty(kernel32, drive: str) -> bool | None:
    """True en un disco mecánico, False en un SSD y None si el dispositivo no lo informa (muchos USB)."""
    handle = kernel32.CreateFileW(f"\\\\.\\{drive[0]}:", 0, _FILE_SHARE_READ_WRITE, None, _OPEN_EXISTING, 0, None)
    if handle in (None, _INVALID_HANDLE):
        return None
    try:
        query = _SeekPenaltyQuery(_SEEK_PENALTY_PROPERTY, 0)
        answer = _SeekPenaltyAnswer()
        returned = wintypes.DWORD()
        done = kernel32.DeviceIoControl(handle, _IOCTL_STORAGE_QUERY_PROPERTY, ctypes.byref(query), ctypes.sizeof(query),
                                        ctypes.byref(answer), ctypes.sizeof(answer), ctypes.byref(returned), None)
        if not done or returned.value < ctypes.sizeof(answer) - 3:
            return None
        return bool(answer.IncursSeekPenalty)
    finally:
        kernel32.CloseHandle(handle)


def _classify(kernel32, drive: str, drive_type: int) -> str:
    if drive_type == _DRIVE_REMOTE:
        return "network"
    if drive_type == _DRIVE_REMOVABLE:
        return "removable"
    if drive_type == _DRIVE_CDROM:
        return "optical"
    penalty = _has_seek_penalty(kernel32, drive)
    if penalty is None:
        return "local"
    return "hdd" if penalty else "ssd"


def local_drives() -> list[str]:
    """Las unidades que son discos fijos de este equipo."""
    kernel32 = _kernel32()
    return [drive for drive in os.listdrives() if kernel32.GetDriveTypeW(drive) == _DRIVE_FIXED]


def list_drives(with_details: bool = True) -> list[Entry]:
    """Unidades del equipo. Sin `with_details` solo lee nombre e icono, para el árbol de carpetas.

    Las unidades de red no se consultan: una desconectada puede tardar mucho en responder.
    """
    kernel32 = _kernel32()
    kernel32.SetThreadErrorMode(_SEM_FAILCRITICALERRORS, None)  # sin avisos de "inserte un disco" en lectores vacíos
    entries: list[Entry] = []
    for drive in os.listdrives():
        drive_type = kernel32.GetDriveTypeW(drive)
        label = filesystem = ""
        if drive_type != _DRIVE_REMOTE:
            label_buffer = ctypes.create_unicode_buffer(261)
            system_buffer = ctypes.create_unicode_buffer(64)
            if kernel32.GetVolumeInformationW(drive, label_buffer, 261, None, None, None, system_buffer, 64):
                label, filesystem = label_buffer.value, system_buffer.value
        kind = _classify(kernel32, drive, drive_type) if with_details else ""
        fallback = {_DRIVE_FIXED: tr("Disco local"), _DRIVE_REMOTE: tr("Unidad de red"), _DRIVE_CDROM: tr("Unidad óptica")}.get(drive_type, tr("Unidad"))
        total = free = None
        if with_details and drive_type != _DRIVE_REMOTE:
            try:
                usage = shutil.disk_usage(drive)
                total, free = usage.total, usage.free
            except OSError:
                pass
        entries.append(Entry(f"{label or fallback} ({drive.rstrip(chr(92))})", drive, True, dir_size=total, is_drive=True,
                             free_space=free, filesystem=filesystem, drive_kind=kind))
    return entries
