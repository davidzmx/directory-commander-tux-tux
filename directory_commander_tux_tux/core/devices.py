"""Los dispositivos de almacenamiento en Linux, como los muestra el panel de lugares del escritorio: los discos y las particiones
que se pueden montar, con su espacio ocupado. Se leen con `lsblk`, y se montan, desmontan y expulsan con `udisksctl`,
que pide la contraseña al usuario si el sistema lo exige."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass

from .i18n import tr

LSBLK_TIMEOUT = 10
UDISKS_TIMEOUT = 120  # puede estar esperando a que el usuario escriba su contraseña
_COLUMNS = "PATH,NAME,TYPE,FSTYPE,LABEL,PARTLABEL,SIZE,RM,HOTPLUG,PARTTYPE,PKNAME"
_SHOWN_TYPES = {"part", "disk", "rom", "crypt", "lvm", "raid0", "raid1", "raid5", "raid6", "raid10", "md"}
# Lo que no es un sistema de archivos que se pueda abrir: el sistema tampoco los muestra.
_HIDDEN_FILESYSTEMS = {"swap", "linux_raid_member", "lvm2_member", "crypto_luks", "zfs_member", "bitlocker", "squashfs", "vmfs_volume_member", "bcache"}
# Particiones del arranque y de la recuperación de Windows: el sistema las oculta (GPT y MBR).
_HIDDEN_PARTITION_TYPES = {
    "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",  # EFI
    "e3c9e316-0b5c-4db8-817d-f92df00215ae",  # reservada de Microsoft
    "de94bba4-06d1-4d40-a16a-bfd50179d6ac",  # recuperación de Windows
    "21686148-6449-6e6f-744e-656564454649",  # arranque BIOS
    "0x27", "0xef", "0x12", "0x84", "0xde",
}
_SYSTEM_MOUNTS = ("/boot", "/boot/efi", "/efi", "[SWAP]")


@dataclass(frozen=True)
class Device:
    path: str  # /dev/sda1
    name: str  # lo que se muestra
    mount_point: str  # vacío si no está montado
    size: int
    removable: bool  # se puede expulsar: un USB o una tarjeta
    drive: str  # el disco entero, para expulsarlo
    used: int | None = None
    total: int | None = None
    fstype: str = ""

    @property
    def free(self) -> int | None:
        return self.total - self.used if self.used is not None and self.total is not None else None

    @property
    def mounted(self) -> bool:
        return bool(self.mount_point)

    @property
    def is_root(self) -> bool:
        return self.mount_point == "/"

    @property
    def used_fraction(self) -> float | None:
        return self.used / self.total if self.used is not None and self.total else None

    @property
    def icon_name(self) -> str:
        if self.is_root:
            return "drive-harddisk-root"
        return "drive-removable-media-usb" if self.removable else "drive-harddisk"


def is_supported() -> bool:
    return shutil.which("lsblk") is not None


def changed(old: list[Device], new: list[Device]) -> bool:
    """Si hay algo que mostrar distinto. Lo ocupado cambia sin parar en la raíz: por debajo de 1 MB no cuenta."""
    def shape(devices):
        return [(device.path, device.name, device.mount_point, device.total, None if device.used is None else device.used >> 20) for device in devices]
    return shape(old) != shape(new)


def _size_text(size: int) -> str:
    value, unit = float(size), "B"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            break
        value /= 1024
    return f"{value:.1f} {unit}".replace(".0 ", " ")


def _flatten(nodes, parent: dict | None = None) -> list[tuple[dict, dict | None]]:
    rows = []
    for node in nodes or ():
        rows.append((node, parent))
        rows.extend(_flatten(node.get("children"), parent or node))
    return rows


def _mounts(node: dict) -> list[str]:
    found = node.get("mountpoints", node.get("mountpoint"))
    found = found if isinstance(found, list) else [found]
    return [point for point in found if isinstance(point, str) and point]


def _shown(node: dict, mounts: list[str]) -> bool:
    fstype = (node.get("fstype") or "").lower()
    if node.get("type") not in _SHOWN_TYPES or not fstype or fstype in _HIDDEN_FILESYSTEMS:
        return False
    if (node.get("parttype") or "").lower() in _HIDDEN_PARTITION_TYPES:
        return False
    return not mounts or any(point not in _SYSTEM_MOUNTS for point in mounts)


def _flag(value) -> bool:
    return value is True or value in ("1", 1)


def parse_lsblk(text: str) -> list[Device]:
    """Los dispositivos de la salida de `lsblk -J -b`, en el orden de sus nombres."""
    data = json.loads(text)
    devices = []
    for node, disk in _flatten(data.get("blockdevices")):
        mounts = _mounts(node)
        if not _shown(node, mounts):
            continue
        mount = "/" if "/" in mounts else next((point for point in mounts if point not in _SYSTEM_MOUNTS), "")
        size = int(node.get("size") or 0)
        removable = any(_flag(item.get(key)) for item in (node, disk or node) for key in ("rm", "hotplug"))
        name = node.get("label") or node.get("partlabel") or tr("Volumen de {size}", size=_size_text(size))
        used = total = None
        if mount:
            try:
                usage = shutil.disk_usage(mount)
                used, total = usage.total - usage.free, usage.total
            except OSError:
                pass
        devices.append(Device(node.get("path") or f"/dev/{node.get('name')}", name, mount, size, removable,
                              (disk or node).get("path") or "", used, total, node.get("fstype") or ""))
    return sorted(devices, key=lambda device: [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", device.path)])


def _run(argv: list[str], timeout: float) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise OSError(tr("Falta el programa {name}", name=argv[0])) from exc
    except subprocess.TimeoutExpired as exc:
        raise OSError(tr("{name} no ha respondido", name=argv[0])) from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip().splitlines()
        raise OSError(message[-1] if message else tr("{name} terminó con el código {code}", name=argv[0], code=result.returncode))
    return result.stdout


def list_devices() -> list[Device]:
    """Lo lee `lsblk`: es rápido y no necesita permisos."""
    try:
        text = _run(["lsblk", "-J", "-b", "-o", _COLUMNS + ",MOUNTPOINTS"], LSBLK_TIMEOUT)
    except OSError:
        text = _run(["lsblk", "-J", "-b", "-o", _COLUMNS + ",MOUNTPOINT"], LSBLK_TIMEOUT)  # un util-linux anterior a la 2.37
    return parse_lsblk(text)


def find(path: str) -> Device | None:
    return next((device for device in list_devices() if device.path == path), None)


def mount(path: str) -> str:
    """Monta el dispositivo y devuelve dónde. Si ya lo estaba, dónde está montado."""
    found = find(path)
    if found is not None and found.mounted:
        return found.mount_point
    output = _run(["udisksctl", "mount", "-b", path], UDISKS_TIMEOUT)
    found = find(path)
    if found is not None and found.mounted:
        return found.mount_point
    match = re.search(r" at (.+?)\.?$", output.strip())
    if match:
        return match.group(1)
    raise OSError(tr("No se pudo montar {device}", device=path))


def unmount(path: str) -> None:
    _run(["udisksctl", "unmount", "-b", path], UDISKS_TIMEOUT)


def eject(device: Device) -> None:
    """Desmonta todo lo del disco y lo apaga, para poder quitarlo con seguridad."""
    for other in list_devices():
        if other.drive == device.drive and other.mounted:
            unmount(other.path)
    _run(["udisksctl", "power-off", "-b", device.drive or device.path], UDISKS_TIMEOUT)
