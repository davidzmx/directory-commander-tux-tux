"""Exportar e importar la configuración por grupos, con las contraseñas SSH/SFTP y entre equipos y sistemas distintos."""
import json
import os

import pytest

pytest.importorskip("paramiko")

from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox  # noqa: E402

from directory_commander_tux_tux.core import remote, settings_io  # noqa: E402
from directory_commander_tux_tux.core.settings_io import Bundle  # noqa: E402
from directory_commander_tux_tux.ui import settings_transfer, sftp  # noqa: E402
from directory_commander_tux_tux.ui.settings_transfer import ENCRYPTED, PLAIN, SettingsTransferDialog  # noqa: E402
from tests.sftp_server import PASSWORD, USER, host_key  # noqa: E402
from tests.test_sftp import answers, connect_pane, memory_keyring, server, window  # noqa: E402,F401

HOME = os.path.expanduser("~")


# ------------------------------------------------------------------ sin interfaz
def test_every_exported_setting_is_in_exactly_one_group():
    seen = [key for group in settings_io.GROUPS for key in group.keys]
    assert len(seen) == len(set(seen)) == len(settings_io.SETTINGS_TYPES)
    assert settings_io.DEFAULT_GROUPS == tuple(group.key for group in settings_io.GROUPS)
    of = settings_io.group_of
    assert of("general_terminal_full_width") == "appearance" and of("folder_tabs") == of("tabgroups") == "layouts"


def test_programs_never_travel(tmp_path):
    path = tmp_path / "c.json"
    programs = {"terminal_shell": "pwsh", "terminal_command": "C:\\x.exe", "ffmpeg_dir": "/opt/ffmpeg", "git_commands": {"status": "git status"},
                "git_custom_commands": [{"label": "s", "command": "status"}], "compare_tool": "meld", "git_program": "/usr/bin/git"}
    settings_io.export_settings(str(path), {"theme": "light", **programs}, settings_io.DEFAULT_GROUPS)
    assert json.loads(path.read_text(encoding="utf-8"))["settings"] == {"theme": "light"}
    # Un archivo de otra versión que sí los traiga: se descartan y se dice.
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "version": 2, "groups": ["appearance", "programs"],
                                "settings": {"theme": "light", **programs}}), encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({"theme": "light"}, list(programs))


def test_the_proposed_name_has_the_date_and_time():
    from datetime import datetime
    assert settings_io.export_name(datetime(2026, 11, 4, 9, 5, 30)) == "configuracion_2026-11-04_09-05.json"


def test_only_the_chosen_groups_are_exported(tmp_path):
    path = tmp_path / "c.json"
    data = {"theme": "light", "favorites": [{"label": "a", "path": "/a"}], "confirm_delete": False, "session": {"tabs": []},
            "general_terminal_full_width": True}
    settings_io.export_settings(str(path), data, ["appearance", "behavior"])
    exported = json.loads(path.read_text(encoding="utf-8"))
    assert exported["groups"] == ["appearance", "behavior"]
    assert exported["settings"] == {"theme": "light", "confirm_delete": False, "general_terminal_full_width": True}
    assert exported["platform"] == settings_io.platform_name() and exported["home"] == HOME and exported["exported"][:4].isdigit()
    bundle = settings_io.read_bundle(str(path))
    assert bundle.groups() == ["appearance", "behavior"] and bundle.exported == exported["exported"]
    assert settings_io.chosen_settings(bundle, ["appearance"]) == {"theme": "light", "general_terminal_full_width": True}


def test_passwords_travel_only_with_the_ssh_group(tmp_path):
    path = tmp_path / "c.json"
    settings_io.export_settings(str(path), {"theme": "light"}, ["appearance"], {"a@h:22": "secreta"}, known_hosts=["h ssh-rsa AAAA"])
    text = path.read_text(encoding="utf-8")
    assert "secreta" not in text and "ssh_known_hosts" not in text


def test_encrypted_passwords_need_the_key(tmp_path):
    path = tmp_path / "c.json"
    settings_io.export_settings(str(path), {"ssh_profiles": []}, [settings_io.SSH], {"ana@srv:22": "secreta", "roto": "x"}, "clave larga")
    assert "secreta" not in path.read_text(encoding="utf-8")
    bundle = settings_io.read_bundle(str(path))
    assert bundle.encrypted and bundle.groups() == [settings_io.SSH]
    with pytest.raises(settings_io.WrongKey):
        settings_io.open_passwords(bundle.passwords, "otra clave")
    assert settings_io.open_passwords(bundle.passwords, "clave larga") == {"ana@srv:22": "secreta"}  # «roto» no es una conexión


def test_tampered_or_abusive_encrypted_passwords_are_refused():
    block = settings_io.seal_passwords({"ana@srv:22": "secreta"}, "clave larga")
    changed = dict(block, data=block["data"][:-4] + ("AAAA" if not block["data"].endswith("AAAA") else "BBBB"))
    with pytest.raises(settings_io.WrongKey):
        settings_io.open_passwords(changed, "clave larga")
    with pytest.raises(ValueError):
        settings_io.open_passwords(dict(block, n=2 ** 30), "clave larga")  # gastaría memoria sin fin


def test_plain_passwords_are_read_without_key(tmp_path):
    path = tmp_path / "c.json"
    settings_io.export_settings(str(path), {}, [settings_io.SSH], {"ana@srv:22": "secreta"})
    bundle = settings_io.read_bundle(str(path))
    assert not bundle.encrypted and settings_io.open_passwords(bundle.passwords) == {"ana@srv:22": "secreta"}


def test_home_paths_follow_this_computer_from_windows():
    bundle = Bundle({"favorites": [{"label": "D", "path": "C:\\Users\\Ana\\Documentos\\x"}, {"label": "E", "path": "C:\\Users\\Anabel"},
                                   {"label": "F", "path": "D:\\datos"}], "saved_sessions": [{"name": "s", "tabs": [["c:\\users\\ana"]]}]},
                    [], "windows", "C:\\Users\\Ana")
    settings = settings_io.chosen_settings(bundle, ["favorites", "layouts"])
    assert [item["path"] for item in settings["favorites"]] == [os.path.join(HOME, "Documentos", "x"), "C:\\Users\\Anabel", "D:\\datos"]
    assert settings["saved_sessions"][0]["tabs"] == [[HOME]]


def test_home_paths_follow_this_computer_from_another_user():
    bundle = Bundle({"ssh_profiles": [{"name": "p", "host": "h", "user": "u", "auth": "key", "key_file": "/home/otra/.ssh/id_ed25519"}],
                     "favorites": [{"label": "x", "path": "sftp://u@h:22/home/otra"}]}, [], settings_io.platform_name(), "/home/otra")
    settings = settings_io.chosen_settings(bundle, ["ssh", "favorites"])
    assert settings["ssh_profiles"][0]["key_file"] == os.path.join(HOME, ".ssh", "id_ed25519")
    assert settings["favorites"][0]["path"] == "sftp://u@h:22/home/otra"  # lo del servidor no es de este equipo


def test_known_hosts_travel_and_never_replace_another_key(tmp_path):
    source, target = str(tmp_path / "a" / "known_hosts"), str(tmp_path / "b" / "known_hosts")
    remote.accept_host_key("srv.example", 22, host_key(), source)
    remote.accept_host_key("10.0.0.5", 2222, host_key(), source)
    lines = remote.known_host_lines([("srv.example", 22), ("10.0.0.5", 2222), ("nadie", 22)], source)
    assert [line.split()[0] for line in lines] == ["srv.example", "[10.0.0.5]:2222"]
    remote.accept_host_key("10.0.0.5", 2222, host_key(1), target)  # en el otro equipo ya había otra clave
    assert remote.add_known_hosts(lines + ["basura", "x ssh-rsa no-es-base64"], target) == (1, ["[10.0.0.5]:2222"])
    assert remote.add_known_hosts(lines, target) == (0, ["[10.0.0.5]:2222"])
    assert len(open(target, encoding="utf-8").read().splitlines()) == 2


# ------------------------------------------------------------------ el cuadro
def test_export_dialog_offers_every_group_and_explains_what_stays(qapp):
    dialog = SettingsTransferDialog()
    assert list(dialog.checks) == [group.key for group in settings_io.GROUPS] and all(check.isChecked() for check in dialog.checks.values())
    dialog.show()
    assert not dialog.key_holder.isVisible() and not dialog.password_warning.isVisible()
    dialog.passwords.setCurrentIndex(dialog.passwords.findData(PLAIN))
    assert dialog.password_warning.isVisible()
    dialog.passwords.setCurrentIndex(dialog.passwords.findData(ENCRYPTED))
    assert dialog.key_holder.isVisible()
    dialog.key.setText("corta")
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and "8" in dialog.notice.text()
    dialog.key.setText("una clave larga")
    dialog.key_again.setText("otra clave larga")
    dialog._accept()
    assert "coinciden" in dialog.notice.text()
    dialog.checks[settings_io.SSH].setChecked(False)
    assert dialog.password_mode() == settings_transfer.NO_PASSWORDS and not dialog.known_hosts() and not dialog.key_holder.isVisible()
    dialog._check_all(False)
    dialog._accept()
    assert "al menos un grupo" in dialog.notice.text()
    dialog.checks["appearance"].setChecked(True)
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted and dialog.groups() == ["appearance"]
    text = " ".join(label.text() for label in dialog.findChildren(type(dialog.notice)))
    assert "administrador de archivos predeterminado" in text and "ffmpeg" in text


def test_import_dialog_shows_only_what_the_file_has(qapp):
    bundle = Bundle({"theme": "light", "ssh_profiles": []}, [], "windows" if settings_io.platform_name() != "windows" else "linux",
                    "C:\\Users\\Ana", "2026-11-04T09:05:30", {"plain": {"a@h:22": "x"}}, ["h ssh-rsa AAAA"])
    dialog = SettingsTransferDialog(None, bundle)
    assert list(dialog.checks) == ["appearance", "ssh"] and all(check.isChecked() for check in dialog.checks.values())
    assert dialog.passwords_wanted() and dialog.known_hosts() and "texto plano" in dialog.import_passwords.text()
    text = " ".join(label.text() for label in dialog.findChildren(type(dialog.notice)))
    assert "Las rutas de la carpeta personal" in text and "04/11/2026 a las 09:05" in text
    dialog.checks["ssh"].setChecked(False)
    assert not dialog.passwords_wanted() and not dialog.known_hosts() and not dialog.import_passwords.isEnabled()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ------------------------------------------------------------------ en la ventana
def _choose(monkeypatch, setup):
    """Lo que se marcaría en el cuadro antes de aceptarlo."""
    def exec_(dialog):
        setup(dialog)
        dialog._accept()
        return dialog.result()
    monkeypatch.setattr(SettingsTransferDialog, "exec", exec_)


def test_ssh_profiles_passwords_and_server_keys_move_to_another_computer(window, server, answers, memory_keyring, monkeypatch, tmp_path):
    connect_pane(window)  # guarda la contraseña en el llavero y la clave del servidor en known_hosts
    window.config["theme"] = "light"
    target = tmp_path / "llevar.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))

    def encrypted(dialog):
        dialog.passwords.setCurrentIndex(dialog.passwords.findData(ENCRYPTED))
        dialog.key.setText("clave del archivo")
        dialog.key_again.setText("clave del archivo")
    _choose(monkeypatch, encrypted)
    window._export_settings_dialog()
    exported = json.loads(target.read_text(encoding="utf-8"))
    assert PASSWORD not in target.read_text(encoding="utf-8") and exported["ssh_passwords"]["cipher"]
    assert exported["ssh_known_hosts"][0].startswith(f"[127.0.0.1]:{server.port} ssh-rsa ")

    # El otro equipo: sin la contraseña, sin la clave del servidor y sin los perfiles.
    remote.disconnect_all()
    memory_keyring.stored.clear()
    sftp._session_passwords.clear()
    os.remove(remote.known_hosts_path())
    window.config[sftp.PROFILES_KEY] = []
    window.config["theme"] = "dark"
    keys = iter(["equivocada", "clave del archivo"])
    asked = []
    monkeypatch.setattr(QInputDialog, "getText", lambda _p, _t, label, *a, **k: (asked.append(label), (next(keys), True))[1])
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text, *a: shown.append(text))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), ""))
    _choose(monkeypatch, lambda dialog: None)
    window._import_settings_dialog()
    assert len(asked) == 2 and asked[1].startswith("No es correcta")
    assert memory_keyring.stored == {(remote.KEYRING_SERVICE, f"{USER}@127.0.0.1:{server.port}"): PASSWORD}
    assert window.config[sftp.PROFILES_KEY][0]["name"] == "Pruebas" and window.config["theme"] == "light"
    assert "1 claves de servidores" in shown[0] and "1 contraseñas" in shown[0]
    answers["questions"].clear()
    answers["passwords"].clear()
    connect_pane(window)
    assert answers["questions"] == [] and answers["passwords"] == []  # ni la huella ni la contraseña


def test_a_group_can_be_imported_on_its_own(window, monkeypatch, tmp_path):
    target = tmp_path / "todo.json"
    window.config["confirm_delete"] = False
    window.config["favorites"] = [{"label": "Antes", "path": str(window.left)}]
    window.export_settings_file(str(target), [group.key for group in settings_io.GROUPS])
    window.config["confirm_delete"] = True
    window.config["favorites"] = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)

    def only_favorites(dialog):
        dialog._check_all(False)
        dialog.checks["favorites"].setChecked(True)
    _choose(monkeypatch, only_favorites)
    window._import_settings_dialog()
    assert window.config["favorites"][0]["label"] == "Antes" and window.config["confirm_delete"] is True
