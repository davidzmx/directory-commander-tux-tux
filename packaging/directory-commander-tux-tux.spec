# Genera dist/directory-commander-tux-tux en el sistema donde se ejecuta (PyInstaller no compila de forma cruzada):
#   python packaging/build.py exe
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

analysis = Analysis(
    [os.path.join(SPECPATH, "run_directory_commander_tux_tux.py")],
    pathex=[ROOT],
    # Qt Multimedia se importa al abrir el primer archivo; los llaveros de keyring, según el sistema, al guardar una contraseña SFTP.
    hiddenimports=["PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "keyring.backends.SecretService", "keyring.backends.kwallet",
                   "keyring.backends.Windows", "keyring.backends.chainer", "keyring.backends.fail"],
    datas=[(os.path.join(ROOT, "directory_commander_tux_tux", "resources"), "directory_commander_tux_tux/resources"),
           (os.path.join(ROOT, "directory_commander_tux_tux", "languages"), "directory_commander_tux_tux/languages")],
    excludes=["tkinter", "PySide6.QtQml", "PySide6.QtQuick"],
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="directory-commander-tux-tux", console=False, icon=os.path.join(SPECPATH, "windows", "app.ico"))
COLLECT(exe, analysis.binaries, analysis.datas, name="directory-commander-tux-tux")
