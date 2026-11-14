"""Punto de entrada de la aplicación."""
from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtWidgets import QApplication

from . import APP_ID, APP_NAME, __version__
from .core import default_manager, i18n
from .core.config import Config, is_portable
from .core.i18n import tr
from .core.pty_session import restore_library_path


def _choose_language(config: Config) -> None:
    folder = config.language_folder
    i18n.set_language(i18n.resolve(config.get("language", i18n.AUTO), QLocale.system().name(), i18n.available_languages(folder)), folder)


def _install_qt_translation(app: QApplication) -> None:
    """Los textos propios de Qt, como los botones Sí y No de los avisos."""
    translator = QTranslator(app)
    if translator.load(f"qtbase_{i18n.current_language()}", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)


def _paths_for_the_explorer(paths: list[str], config: Config) -> list[str]:
    """Las carpetas que el usuario quiere seguir abriendo en el Explorador se las pasa con la orden «explore», para no volver aquí en círculo."""
    exceptions = config.get("file_manager_exceptions") or []
    outside = [path for path in paths if default_manager.is_exception(path, exceptions)] if sys.platform == "win32" else []
    for path in outside:
        os.startfile(path, "explore")
    return outside


def _apply_installer_choices(config: Config, args) -> int:
    """Lo que se eligió al instalar. Una carpeta vacía o que no existe no cambia nada: se sigue buscando en el PATH."""
    for key, folder in (("ffmpeg_dir", args.ffmpeg_dir), ("exiftool_dir", args.exiftool_dir)):
        if folder and os.path.isdir(folder):
            config[key] = folder
    if args.replace_file_manager and sys.platform == "win32" and not is_portable():
        default_manager.become_windows_default(args.win_e)
    config.save()
    return 0


def main(argv: list[str] | None = None) -> int:
    restore_library_path()
    config = Config()
    _choose_language(config)  # antes de importar la interfaz, que calcula algunos textos al importarse
    parser = argparse.ArgumentParser(prog=APP_ID, description=tr("{APP_NAME}: gestor de archivos de doble panel", APP_NAME=APP_NAME))
    parser.add_argument("paths", nargs="*", help=tr("carpetas para abrir (hasta dos, una por panel)"))
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--restore-file-manager", action="store_true", help=tr("deja de ser el administrador de archivos del sistema y sale"))
    # Las usa el instalador de Windows para aplicar lo que se eligió en él. Se aplican y el programa sale sin abrir la ventana.
    parser.add_argument("--replace-file-manager", action="store_true", help=tr("reemplaza al Explorador de archivos (solo en Windows) y sale"))
    parser.add_argument("--win-e", action="store_true", help=tr("con --replace-file-manager, abre también este programa con Win+E"))
    parser.add_argument("--ffmpeg-dir", metavar=tr("CARPETA"), help=tr("guarda la carpeta que contiene ffmpeg y sale"))
    parser.add_argument("--exiftool-dir", metavar=tr("CARPETA"), help=tr("guarda la carpeta que contiene exiftool y sale"))
    args, qt_args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    args.paths = [default_manager.clean_argument(path) for path in args.paths]
    if args.restore_file_manager:
        default_manager.restore_system_default(config.get("file_manager_previous", ""))
        return 0
    if args.replace_file_manager or args.ffmpeg_dir is not None or args.exiftool_dir is not None:
        return _apply_installer_choices(config, args)

    from .ui.icons import app_icon
    from .ui.main_window import MainWindow
    from .ui.single_instance import PathServer, send_to_running, server_name

    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")  # base común: el aspecto lo define el tema en todos los escritorios
    _install_qt_translation(app)

    outside = _paths_for_the_explorer(args.paths, config)
    args.paths = [path for path in args.paths if path not in outside]
    if outside and not args.paths:
        return 0
    name = server_name(config.directory)
    if args.paths and config.get("file_manager_new_tab", True) and send_to_running(name, [os.path.abspath(path) for path in args.paths]):
        return 0

    window = MainWindow(config, args.paths)
    window.show()
    server = PathServer(name, window.open_external_paths, window)  # noqa: F841 - vive con la ventana
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
