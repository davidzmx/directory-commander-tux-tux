"""Los paquetes: la versión portable, el .deb, el instalador y el cambio de nombre del programa."""
from __future__ import annotations

import ast
import gzip
import importlib.util
import io
import json
import os
import re
import struct
import sys
import tarfile
import zipfile

import pytest
from PySide6.QtWidgets import QApplication

from directory_commander_tux_tux import APP_ID, APP_NAME, __version__
from directory_commander_tux_tux.core import config as config_module
from directory_commander_tux_tux.core import default_manager
from directory_commander_tux_tux.core.config import Config, config_dir, is_portable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
specification = importlib.util.spec_from_file_location("packaging_build", os.path.join(ROOT, "packaging", "build.py"))
build = importlib.util.module_from_spec(specification)
sys.modules["packaging_build"] = build
specification.loader.exec_module(build)


def fake_app(tmp_path):
    folder = tmp_path / APP_ID
    (folder / "_internal" / "sub").mkdir(parents=True)
    (folder / APP_ID).write_bytes(b"programa")
    os.chmod(folder / APP_ID, 0o755)
    (folder / "_internal" / "sub" / "dato.txt").write_text("hola")
    return str(folder)


def test_the_name_of_the_program():
    assert APP_NAME == "Directory Commander Tux Tux" and APP_ID == "directory-commander-tux-tux"


# ------------------------------------------------------------------ versión portable
def test_portable_zip_has_the_marker_inside_its_folder(tmp_path):
    out = build.make_portable(fake_app(tmp_path), str(tmp_path / "salida"), "windows", "1.2.3")
    assert os.path.basename(out) == f"{APP_ID}-1.2.3-windows-portable.zip"
    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
    top = f"{APP_ID}-1.2.3-windows-portable"
    assert {f"{top}/{build.PORTABLE_MARKER}", f"{top}/{APP_ID}", f"{top}/_internal/sub/dato.txt"} == names


def test_portable_tar_keeps_folder_and_marker(tmp_path):
    out = build.make_portable(fake_app(tmp_path), str(tmp_path / "salida"), "linux", "1.2.3")
    with tarfile.open(out) as archive:
        names = archive.getnames()
    top = f"{APP_ID}-1.2.3-linux-portable"
    assert f"{top}/{build.PORTABLE_MARKER}" in names and f"{top}/_internal/sub/dato.txt" in names


def frozen_at(monkeypatch, folder):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", os.path.join(str(folder), "programa"))
    monkeypatch.delenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", raising=False)


def test_with_the_marker_the_configuration_stays_next_to_the_program(tmp_path, monkeypatch):
    frozen_at(monkeypatch, tmp_path)
    assert not is_portable()
    (tmp_path / build.PORTABLE_MARKER).write_text("x")
    assert is_portable() and config_dir() == str(tmp_path / "config")


def test_the_marker_names_match():
    assert build.PORTABLE_MARKER == config_module.PORTABLE_MARKER


def test_from_the_source_code_the_marker_means_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    (tmp_path / build.PORTABLE_MARKER).write_text("x")
    assert not is_portable()


def test_portable_does_not_take_the_settings_of_the_user(tmp_path, monkeypatch):
    home = tmp_path / "usuario"
    previous = home / ("DirectoryExplorerTux" if sys.platform == "win32" else "directory-explorer-tux")
    previous.mkdir(parents=True)
    (previous / "config.json").write_text(json.dumps({"config_version": 2, "theme": "light"}), encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    program = tmp_path / "programa"
    program.mkdir()
    frozen_at(monkeypatch, program)
    (program / build.PORTABLE_MARKER).write_text("x")
    config = Config()
    assert config["theme"] != "light" and config.directory == str(program / "config")
    config.save()
    assert (program / "config" / "config.json").is_file() and not (home / ("DirectoryCommanderTuxTux" if sys.platform == "win32" else APP_ID)).exists()


def test_the_portable_version_cannot_replace_the_file_manager(tmp_path, monkeypatch):
    from directory_commander_tux_tux.ui import system_pages

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(system_pages, "is_portable", lambda: True)
    called = []
    monkeypatch.setattr(system_pages.default_manager, "restore_system_default", lambda *a: called.append(a))
    monkeypatch.setattr(system_pages.default_manager, "replace_explorer", lambda *a: called.append(a))
    config = Config(str(tmp_path))
    page = system_pages.FileManagerPage(config)
    page.apply_to(config)
    assert not called and page.portable_notice.text()


# ------------------------------------------------------------------ cambio de nombre
def previous_config(home, folder, **values):
    (home / folder).mkdir(parents=True)
    (home / folder / "config.json").write_text(json.dumps({"config_version": 16, **values}), encoding="utf-8")


def clean_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def test_the_settings_of_the_previous_name_are_kept(tmp_path, monkeypatch):
    clean_environment(tmp_path, monkeypatch)
    previous_config(tmp_path, "DirectoryExplorerTux" if sys.platform == "win32" else "directory-explorer-tux", theme="light")
    config = Config()
    assert config["theme"] == "light" and (tmp_path / ("DirectoryCommanderTuxTux" if sys.platform == "win32" else APP_ID) / "config.json").is_file()
    assert (tmp_path / ("DirectoryExplorerTux" if sys.platform == "win32" else "directory-explorer-tux") / "config.json").is_file()  # el original no se toca


def test_the_latest_previous_name_wins(tmp_path, monkeypatch):
    clean_environment(tmp_path, monkeypatch)
    windows = sys.platform == "win32"
    previous_config(tmp_path, "DirectoryTux" if windows else "directory-tux", theme="dark")
    previous_config(tmp_path, "DirectoryExplorerTux" if windows else "directory-explorer-tux", theme="light")
    assert Config()["theme"] == "light"


def test_translations_of_the_user_come_along(tmp_path, monkeypatch):
    clean_environment(tmp_path, monkeypatch)
    old = "DirectoryExplorerTux" if sys.platform == "win32" else "directory-explorer-tux"
    previous_config(tmp_path, old)
    (tmp_path / old / "languages").mkdir()
    (tmp_path / old / "languages" / "fr.json").write_text("{}")
    config = Config()
    assert os.path.isfile(os.path.join(config.directory, "languages", "fr.json"))


def test_the_app_image_is_launched_by_its_own_file(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPIMAGE", "/home/x/programa.AppImage")
    assert default_manager.launch_arguments() == ["/home/x/programa.AppImage"]  # sys.executable está en un montaje que desaparece al cerrar


# ------------------------------------------------------------------ .deb
def read_ar(path):
    with open(path, "rb") as source:
        assert source.read(8) == b"!<arch>\n"
        members = {}
        while header := source.read(60):
            assert header[58:60] == b"`\n"
            size = int(header[48:58])
            members[header[:16].decode().strip().rstrip("/")] = source.read(size)
            if size % 2:
                source.read(1)
    return members


def test_the_deb_is_well_formed(tmp_path):
    out = build.make_deb(fake_app(tmp_path), str(tmp_path / "salida"), "1.2.3")
    assert os.path.basename(out) == f"{APP_ID}_1.2.3_amd64.deb"
    members = read_ar(out)
    assert list(members) == ["debian-binary", "control.tar.gz", "data.tar.gz"] and members["debian-binary"] == b"2.0\n"  # dpkg exige ese orden
    with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"])) as control:
        text = control.extractfile("./control").read().decode()
        assert control.getmember("./postinst").mode == 0o755
    for field in (f"Package: {APP_ID}", "Version: 1.2.3", "Architecture: amd64", "Depends:", "Description:"):
        assert field in text
    assert text.endswith("\n") and "libxcb-cursor0" in text


def test_the_deb_installs_the_program_its_link_and_its_entry(tmp_path):
    members = read_ar(build.make_deb(fake_app(tmp_path), str(tmp_path / "salida"), "1.2.3"))
    with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"])) as data:
        names = data.getnames()
        link = data.getmember(f"./usr/bin/{APP_ID}")
        program = data.getmember(f"./opt/{APP_ID}/{APP_ID}")
        desktop = data.extractfile(f"./usr/share/applications/{APP_ID}.desktop").read().decode()
        assert link.issym() and link.linkname == f"/opt/{APP_ID}/{APP_ID}"
        assert program.mode == 0o755 and data.getmember(f"./opt/{APP_ID}/_internal/sub/dato.txt").mode == 0o644
        assert f"./usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg" in names and f"./opt/{APP_ID}" in names
    assert f"Name={APP_NAME}" in desktop and f"Exec={APP_ID} %F" in desktop and "MimeType=inode/directory;" in desktop


@pytest.mark.skipif(sys.platform == "win32", reason="crear enlaces en Windows pide permisos")
def test_the_deb_keeps_the_links_of_the_program(tmp_path):
    folder = fake_app(tmp_path)
    os.symlink("sub", os.path.join(folder, "_internal", "enlace_carpeta"))
    os.symlink("sub/dato.txt", os.path.join(folder, "_internal", "enlace_archivo"))
    members = read_ar(build.make_deb(folder, str(tmp_path / "salida"), "1.2.3"))
    with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"])) as data:
        assert data.getmember(f"./opt/{APP_ID}/_internal/enlace_carpeta").issym() and data.getmember(f"./opt/{APP_ID}/_internal/enlace_archivo").linkname == "sub/dato.txt"


def test_the_newest_inno_setup_is_found(tmp_path, monkeypatch):
    for version in ("Inno Setup 6", "Inno Setup 7"):
        (tmp_path / version).mkdir()
        (tmp_path / version / "ISCC.exe").write_text("x")
    monkeypatch.setattr(build.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    assert build.find_inno_setup() == str(tmp_path / "Inno Setup 7" / "ISCC.exe")


def test_what_is_shipped_says_what_it_carries():
    for name in build.LEGAL_FILES:
        assert os.path.isfile(os.path.join(ROOT, name))
    with open(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), encoding="utf-8") as source:
        text = source.read()
    for component in ("Qt", "PySide6", "FFmpeg", "LGPL", "Python", "OpenSSL"):
        assert component in text


def test_ar_members_are_padded_to_even_length():
    assert len(build._ar_member("a", b"123", 0)) == 60 + 4 and len(build._ar_member("a", b"1234", 0)) == 60 + 4


def test_the_tar_of_the_deb_is_really_gzip():
    data = build._tar_gz(lambda archive: build._add_bytes(archive, "./x", b"hola"))
    assert gzip.decompress(data)[:1] == b"." and data[:2] == b"\x1f\x8b"


# ------------------------------------------------------------------ AppImage
def test_the_app_dir_has_what_appimage_needs(tmp_path):
    app_dir = build.make_appdir(fake_app(tmp_path), str(tmp_path / "App.AppDir"))
    for relative in ("AppRun", f"{APP_ID}.desktop", f"{APP_ID}.svg", f"usr/lib/{APP_ID}/{APP_ID}"):
        assert os.path.isfile(os.path.join(app_dir, relative))
    with open(os.path.join(app_dir, "AppRun"), "rb") as script:
        text = script.read()
    assert text.startswith(b"#!/bin/sh\n") and b"\r" not in text and f"usr/lib/{APP_ID}/{APP_ID}".encode() in text


# ------------------------------------------------------------------ Windows
def test_the_icon_has_every_size():
    images = {16: b"a" * 10, 256: b"b" * 20}
    data = build.ico_bytes(images)
    assert struct.unpack("<HHH", data[:6]) == (0, 1, 2)
    width, _height, _colors, _reserved, _planes, _bits, size, offset = struct.unpack("<BBBBHHII", data[6:22])
    assert width == 16 and size == 10 and data[offset:offset + size] == b"a" * 10
    assert data[22] == 0  # 256 se escribe como 0


def test_the_icon_in_the_repository_is_valid():
    with open(build.ICON_ICO, "rb") as source:
        data = source.read()
    assert struct.unpack("<HHH", data[:6]) == (0, 1, len(build.ICON_SIZES))


def installer_text():
    with open(build.INSTALLER_SCRIPT, encoding="utf-8") as source:
        return source.read()


def test_the_installer_needs_no_administrator_and_undoes_the_replacement():
    text = installer_text()
    assert f'#define AppName "{APP_NAME}"' in text and f'#define AppId "{APP_ID}"' in text and f'"{__version__}"' in text
    assert "PrivilegesRequired=lowest" in text and "--restore-file-manager" in text and "[UninstallRun]" in text


def test_the_installer_offers_the_optional_programs_and_the_explorer_and_the_program_applies_them():
    text = installer_text()
    for flag in ("--ffmpeg-dir", "--exiftool-dir", "--replace-file-manager", "--win-e"):
        assert flag in text
    for language in ("spanish", "english"):
        for message in ("ToolsCaption", "ToolsSubCaption", "FFmpegPrompt", "ExifToolPrompt", "ExplorerCaption", "ExplorerSubCaption", "ReplaceOption", "WinEOption"):
            assert f"{language}.{message}=" in text
    assert "Preferencias › General › Programas" in text and "Preferencias › Administrador de archivos" in text
    assert "ExecAsOriginalUser" in text  # instalado para todos, lo elegido es del usuario que instala, no del administrador


def test_the_files_of_the_installer_exist():
    text = installer_text()
    folder = os.path.dirname(build.INSTALLER_SCRIPT)
    for name in ("app.ico", "..\\..\\LICENSE"):
        assert name in text and os.path.isfile(os.path.join(folder, name.replace("\\", os.sep)))


def test_missing_tools_are_explained(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "DIST", str(tmp_path))
    (tmp_path / APP_ID).mkdir()
    monkeypatch.setattr(build, "find_inno_setup", lambda: "")
    with pytest.raises(SystemExit, match="Inno Setup"):
        build.make_windows_installer()
    monkeypatch.delenv("APPIMAGETOOL", raising=False)
    monkeypatch.setattr(build.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="appimagetool"):
        build.make_appimage()


# ------------------------------------------------------------------ los README
READMES = {"es": "README.md", "en": "README.en.md"}


def readme(language):
    with open(os.path.join(ROOT, READMES[language]), encoding="utf-8") as source:
        return source.read()


@pytest.mark.parametrize("language", READMES)
def test_every_image_of_the_readme_exists_and_is_in_its_language(language):
    images = re.findall(r"\]\((docs/[^)]+\.png)\)", readme(language))
    assert len(images) >= 8
    for image in images:
        assert image.startswith(f"docs/capturas/{language}/") and os.path.isfile(os.path.join(ROOT, image))


def test_each_readme_links_to_the_other():
    assert "(README.en.md)" in readme("es") and "(README.md)" in readme("en")


def test_both_readmes_have_the_same_structure():
    headings = {language: re.findall(r"^#+ ", readme(language), re.M) for language in READMES}
    assert headings["es"] == headings["en"]
    assert readme("es").count("\n- ") == readme("en").count("\n- ")


@pytest.mark.parametrize("language,anchor", [("es", "#estilos-una-disposición-para-cada-tarea"), ("en", "#styles-one-layout-for-each-task")])
def test_the_link_to_the_styles_points_to_its_section(language, anchor):
    text = readme(language)
    assert f"]({anchor})" in text
    title = next(line[3:] for line in text.splitlines() if line.startswith("## ") and ":" in line and ("Estilos" in line or "Styles" in line))
    assert "#" + title.lower().replace(":", "").replace(" ", "-") == anchor


# ------------------------------------------------------------------ que arranque en cualquier sistema
_WINDOWS_ONLY_MODULES = {"winreg", "msvcrt", "winsound", "_winapi"}
_WINDOWS_ONLY_NAMES = {"WINFUNCTYPE", "windll", "oledll", "WinDLL", "OleDLL", "WinError", "HRESULT", "FormatError", "get_last_error", "set_last_error"}


def _module_level_statements(tree):
    """Lo que se ejecuta al importar, salvo lo que está dentro de un `if`: ahí se comprueba el sistema antes."""
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, ast.Try):
            yield from (item for item in node.body if isinstance(item, (ast.Import, ast.ImportFrom)))


@pytest.mark.parametrize("path", [os.path.join(root, name) for root, _folders, names in os.walk(os.path.join(ROOT, "directory_commander_tux_tux")) for name in names if name.endswith(".py")])
def test_importing_does_not_need_windows(path):
    """Un `from ctypes import WINFUNCTYPE` suelto impide arrancar en Linux, y en Windows no se nota."""
    with open(path, encoding="utf-8") as source:
        tree = ast.parse(source.read())
    for node in _module_level_statements(tree):
        if isinstance(node, ast.Import):
            assert not {alias.name for alias in node.names} & _WINDOWS_ONLY_MODULES, path
        else:
            assert node.module not in _WINDOWS_ONLY_MODULES, path
            if node.module == "ctypes":
                assert not {alias.name for alias in node.names} & _WINDOWS_ONLY_NAMES, path
