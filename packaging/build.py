"""Genera los paquetes para repartir el programa. Hay que ejecutarlo en cada sistema: PyInstaller no compila de forma cruzada.

    python packaging/build.py exe          el programa, en dist/
    python packaging/build.py portable     un .zip en Windows o un .tar.gz en Linux, que se descomprime y se usa
    python packaging/build.py installer    Windows: instalador con Inno Setup. Linux: paquete .deb
    python packaging/build.py appimage     Linux: AppImage, con appimagetool
    python packaging/build.py all          todo lo que se pueda en este sistema
"""
from __future__ import annotations

import argparse
import glob
import gzip
import io
import os
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from directory_commander_tux_tux import APP_ID, APP_NAME, __version__  # noqa: E402

PACKAGE = APP_ID.replace("-", "_")
DIST = os.path.join(ROOT, "dist")
ICON_SVG = os.path.join(ROOT, PACKAGE, "resources", f"{APP_ID}.svg")
ICON_ICO = os.path.join(ROOT, "packaging", "windows", "app.ico")
INSTALLER_SCRIPT = os.path.join(ROOT, "packaging", "windows", "installer.iss")
DESKTOP_FILE = os.path.join(ROOT, "packaging", "linux", f"{APP_ID}.desktop")
LEGAL_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md")  # lo que se reparte lleva bibliotecas de terceros: debe decirlo
PORTABLE_MARKER = "portable.txt"
PORTABLE_TEXT = ("Este archivo activa el modo portable: la configuración se guarda en la carpeta config, junto al programa.\n"
                 "Si lo borras, el programa usará la configuración de tu usuario.\n")
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
# Lo que Qt necesita del sistema y no viene en el paquete.
DEB_DEPENDS = "libc6, libegl1, libgl1, libxkbcommon0, libxkbcommon-x11-0, libdbus-1-3, libfontconfig1, libglib2.0-0, libxcb-cursor0, libpulse0"


def _architecture() -> str:
    return "x64" if sys.platform == "win32" else "amd64"


def _system() -> str:
    return "windows" if sys.platform == "win32" else "linux"


def build_exe() -> str:
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", DIST, "--workpath", os.path.join(ROOT, "build"), os.path.join(ROOT, "packaging", f"{APP_ID}.spec")], check=True, cwd=ROOT)
    folder = os.path.join(DIST, APP_ID)
    for name in LEGAL_FILES:
        shutil.copy(os.path.join(ROOT, name), folder)
    return folder


def _app_folder() -> str:
    folder = os.path.join(DIST, APP_ID)
    if not os.path.isdir(folder):
        raise SystemExit(f"No está {folder}: primero «build.py exe»")
    return folder


# ------------------------------------------------------------------ el icono de Windows
def ico_bytes(images: dict[int, bytes]) -> bytes:
    """Un .ico con cada tamaño guardado como PNG, que es lo que admite Windows desde Vista."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, data = b"", b""
    for size, png in sorted(images.items()):
        shown = 0 if size >= 256 else size  # 0 significa 256 en este formato
        entries += struct.pack("<BBBBHHII", shown, shown, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return header + entries + data


def make_icon(out: str = ICON_ICO) -> str:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(ICON_SVG)
    images = {}
    for size in ICON_SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        images[size] = bytes(buffer.data())
    del app
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as target:
        target.write(ico_bytes(images))
    return out


# ------------------------------------------------------------------ versión portable
def _portable_name(system: str, version: str) -> str:
    return f"{APP_ID}-{version}-{system}-portable"


def make_portable(folder: str | None = None, out_dir: str = DIST, system: str | None = None, version: str = __version__) -> str:
    """Todo dentro de una carpeta, con la marca que hace que la configuración se quede en ella."""
    folder = folder or _app_folder()
    system = system or _system()
    name = _portable_name(system, version)
    os.makedirs(out_dir, exist_ok=True)
    if system == "windows":
        out = os.path.join(out_dir, f"{name}.zip")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path, relative in _walk(folder):
                archive.write(path, f"{name}/{relative}")
            archive.writestr(f"{name}/{PORTABLE_MARKER}", PORTABLE_TEXT)
        return out
    out = os.path.join(out_dir, f"{name}.tar.gz")
    with tarfile.open(out, "w:gz") as archive:
        archive.add(folder, name, filter=_no_owner)  # conserva los permisos de ejecución
        info = tarfile.TarInfo(f"{name}/{PORTABLE_MARKER}")
        data = PORTABLE_TEXT.encode("utf-8")
        info.size, info.mode, info.mtime = len(data), 0o644, int(time.time())
        archive.addfile(info, io.BytesIO(data))
    return out


def _walk(folder: str):
    for root, folders, names in os.walk(folder):
        # Un enlace a una carpeta no es una carpeta que recorrer, pero tampoco se puede perder.
        for name in sorted([*names, *(item for item in folders if os.path.islink(os.path.join(root, item)))]):
            path = os.path.join(root, name)
            yield path, os.path.relpath(path, folder).replace(os.sep, "/")


def _no_owner(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


# ------------------------------------------------------------------ instalador de Windows
def find_inno_setup() -> str:
    found = shutil.which("iscc")
    if found:
        return found
    roots = (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"))
    versions = sorted((path for root in roots if root for path in glob.glob(os.path.join(root, "Inno Setup *", "ISCC.exe"))), reverse=True)
    return versions[0] if versions else ""


def make_windows_installer() -> str:
    _app_folder()
    compiler = find_inno_setup()
    if not compiler:
        raise SystemExit("Hace falta Inno Setup 6 para generar el instalador: https://jrsoftware.org/isinfo.php")
    subprocess.run([compiler, f"/DAppVersion={__version__}", INSTALLER_SCRIPT], check=True, cwd=ROOT)
    return os.path.join(DIST, f"{APP_ID}-{__version__}-windows-setup.exe")


# ------------------------------------------------------------------ paquete .deb
def _ar_member(name: str, data: bytes, modified: int) -> bytes:
    header = f"{name + '/':<16}{modified:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode("ascii")
    return header + data + (b"\n" if len(data) % 2 else b"")


def _tar_gz(fill) -> bytes:
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
        fill(archive)
    return raw.getvalue()


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(data), mode, int(time.time())
    archive.addfile(info, io.BytesIO(data))


def _add_folders(archive: tarfile.TarFile, path: str, done: set[str]) -> None:
    """Un .deb lista también las carpetas, y con sus permisos."""
    parts = path.strip("/").split("/")
    for length in range(1, len(parts) + 1):
        folder = "./" + "/".join(parts[:length])
        if folder not in done:
            done.add(folder)
            info = tarfile.TarInfo(folder)
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, int(time.time())
            archive.addfile(info)


def _is_executable(path: str, relative: str) -> bool:
    """En Windows el sistema de archivos no lo guarda: solo se puede saber por el nombre. Sirve para probar el paquete, no para repartirlo."""
    if sys.platform != "win32":
        return bool(os.stat(path).st_mode & stat.S_IXUSR)
    return relative == APP_ID


def make_deb(folder: str | None = None, out_dir: str = DIST, version: str = __version__) -> str:
    """Se arma aquí mismo, sin dpkg-deb: es un archivo ar con dos tar. Instala en /opt, con un enlace en /usr/bin."""
    folder = folder or _app_folder()
    target = f"opt/{APP_ID}"
    size_kb = sum(os.lstat(path).st_size for path, _relative in _walk(folder)) // 1024

    def control(archive: tarfile.TarFile) -> None:
        text = (f"Package: {APP_ID}\nVersion: {version}\nArchitecture: amd64\nMaintainer: {APP_NAME}\nInstalled-Size: {size_kb}\nDepends: {DEB_DEPENDS}\nSection: utils\nPriority: optional\n"
                f"Description: Gestor de archivos de doble panel\n {APP_NAME}: gestor de archivos de doble panel para KDE y GNOME.\n")
        _add_bytes(archive, "./control", text.encode("utf-8"))
        script = "#!/bin/sh\nset -e\ncommand -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true\n"
        _add_bytes(archive, "./postinst", script.encode("ascii"), 0o755)
        _add_bytes(archive, "./postrm", script.encode("ascii"), 0o755)

    def content(archive: tarfile.TarFile) -> None:
        done: set[str] = set()
        for path, relative in _walk(folder):
            name = f"{target}/{relative}"
            _add_folders(archive, os.path.dirname(name), done)
            if os.path.islink(path):
                link = tarfile.TarInfo(f"./{name}")
                link.type, link.linkname, link.mode, link.mtime = tarfile.SYMTYPE, os.readlink(path), 0o777, int(time.time())
                archive.addfile(link)
                continue
            with open(path, "rb") as source:
                _add_bytes(archive, f"./{name}", source.read(), 0o755 if _is_executable(path, relative) else 0o644)
        _add_folders(archive, "usr/bin", done)
        link = tarfile.TarInfo(f"./usr/bin/{APP_ID}")
        link.type, link.linkname, link.mode, link.mtime = tarfile.SYMTYPE, f"/{target}/{APP_ID}", 0o777, int(time.time())
        archive.addfile(link)
        _add_folders(archive, "usr/share/applications", done)
        with open(DESKTOP_FILE, "rb") as source:
            _add_bytes(archive, f"./usr/share/applications/{APP_ID}.desktop", source.read())
        _add_folders(archive, "usr/share/icons/hicolor/scalable/apps", done)
        with open(ICON_SVG, "rb") as source:
            _add_bytes(archive, f"./usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg", source.read())

    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{APP_ID}_{version}_amd64.deb")
    now = int(time.time())
    with open(out, "wb") as package:
        package.write(b"!<arch>\n")
        package.write(_ar_member("debian-binary", b"2.0\n", now))
        package.write(_ar_member("control.tar.gz", _tar_gz(control), now))
        package.write(_ar_member("data.tar.gz", _tar_gz(content), now))
    return out


# ------------------------------------------------------------------ AppImage
APP_RUN = f"""#!/bin/sh
HERE=$(dirname "$(readlink -f "$0")")
exec "$HERE/usr/lib/{APP_ID}/{APP_ID}" "$@"
"""


def make_appdir(folder: str, app_dir: str) -> str:
    if os.path.exists(app_dir):
        shutil.rmtree(app_dir)
    shutil.copytree(folder, os.path.join(app_dir, "usr", "lib", APP_ID), symlinks=True)
    with open(os.path.join(app_dir, "AppRun"), "w", encoding="utf-8", newline="\n") as script:
        script.write(APP_RUN)
    os.chmod(os.path.join(app_dir, "AppRun"), 0o755)
    shutil.copy(DESKTOP_FILE, app_dir)
    shutil.copy(ICON_SVG, app_dir)
    return app_dir


def make_appimage() -> str:
    tool = os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool") or ""
    if not tool:
        raise SystemExit("Hace falta appimagetool para generar el AppImage: https://github.com/AppImage/appimagetool")
    app_dir = make_appdir(_app_folder(), os.path.join(ROOT, "build", f"{APP_ID}.AppDir"))
    out = os.path.join(DIST, f"{APP_ID}-{__version__}-x86_64.AppImage")
    subprocess.run([tool, app_dir, out], check=True, env={**os.environ, "ARCH": "x86_64"})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Genera los paquetes de {APP_NAME}")
    parser.add_argument("what", choices=("exe", "icon", "portable", "installer", "appimage", "all"))
    what = parser.parse_args().what
    windows = sys.platform == "win32"
    if what == "icon":
        print(make_icon())
    if what in ("exe", "all"):
        print(build_exe())
    if what in ("portable", "all"):
        print(make_portable())
    if what in ("installer", "all"):
        print(make_windows_installer() if windows else make_deb())
    if what == "appimage" or (what == "all" and not windows):
        if windows:
            raise SystemExit("El AppImage solo se puede generar en Linux")
        print(make_appimage())


if __name__ == "__main__":
    main()
