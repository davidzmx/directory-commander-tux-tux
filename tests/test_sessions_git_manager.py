"""Sesiones guardadas, git en las terminales y ser el administrador de archivos del sistema."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QInputDialog, QLabel, QMessageBox

from directory_commander_tux_tux.core import bar_buttons, default_manager, file_compare, git_tools, sessions, styles
from directory_commander_tux_tux.core.config import Config
from directory_commander_tux_tux.core.shells import CMD, FISH, OTHER, POSIX, POWERSHELL
from directory_commander_tux_tux.ui.git_bar import CommitDialog, FilesDialog, GitBar
from directory_commander_tux_tux.ui.main_window import GENERAL_TERMINAL, MainWindow
from directory_commander_tux_tux.ui.bar_buttons_page import BarButtonsPage
from directory_commander_tux_tux.ui import theme
from directory_commander_tux_tux.ui.compare_view import CompareWindow, compare_files, compare_images
from directory_commander_tux_tux.ui.find_panel import FindWindow
from directory_commander_tux_tux.ui.folder_picker import FolderPicker
from directory_commander_tux_tux.ui.sessions_page import SessionsPage
from directory_commander_tux_tux.ui.single_instance import PathServer, send_to_running, server_name
from directory_commander_tux_tux.ui.system_pages import ComparePage, FileManagerPage, GitPage


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, question, dangerous=False: True)
    home = tmp_path / "casa"
    home.mkdir()
    created = MainWindow(Config(str(tmp_path / "config")), [str(home)])
    created.show()
    QApplication.processEvents()
    yield created
    created.close()
    created.deleteLater()
    QApplication.processEvents()


def folders(tmp_path, *names):
    result = []
    for name in names:
        folder = tmp_path / name
        folder.mkdir(exist_ok=True)
        result.append(str(folder))
    return result


# ------------------------------------------------------------------ sesiones
def test_default_session_name_has_the_date_and_time():
    assert sessions.default_name(datetime(2026, 11, 2, 9, 5, 7)) == "sesion_20261102_090507"


def test_stored_replaces_a_session_with_the_same_name_and_keeps_its_place():
    items = sessions.stored([], "uno", {"tabs": []})
    items = sessions.stored(items, "dos", {"tabs": []})
    items = sessions.stored(items, " UNO ", {"tabs": [["a"]]})
    assert sessions.names(items) == ["uno", "dos"] and sessions.find(items, "uno") == {"tabs": [["a"]]}


def test_rename_refuses_empty_and_used_names_but_allows_changing_the_case():
    items = sessions.stored(sessions.stored([], "uno", {}), "dos", {})
    for bad in ("", "  ", "DOS"):
        with pytest.raises(ValueError):
            sessions.renamed(items, "uno", bad)
    assert sessions.names(sessions.renamed(items, "uno", "UNO")) == ["UNO", "dos"]
    assert sessions.names(sessions.removed(items, "uno")) == ["dos"]


@pytest.mark.parametrize("raw", [None, 3, [3], [{"name": "", "data": {}}], [{"name": "a"}], [{"name": "a", "data": 1}], [{"name": 3, "data": {}}],
                                 [{"name": "a", "data": {"tabs": 3}}], [{"name": "a", "data": {"geometry": 3}}]])
def test_damaged_sessions_are_left_out(raw):
    assert sessions.clean(raw) == []


def test_saving_and_loading_brings_back_panes_tabs_and_names(window, tmp_path, monkeypatch):
    uno, dos, tres, otra = folders(tmp_path, "uno", "dos", "tres", "otra")
    group = window.groups[0]
    group.current.navigate(uno)
    named = group.add_tab(dos, activate=True)
    named.tab_name = "Fotos"
    window.groups[1].current.navigate(tres)
    window.set_dual("Horizontal")
    window.save_named_session("trabajo")
    assert window.session_box.currentText() == "trabajo"
    assert sessions.names(Config(window.config.directory).get("saved_sessions")) == ["trabajo"]  # ya está en disco
    expected_paths = [[v.path for v in g.views()] for g in window.groups[:2]]

    group.close_all_tabs()
    group.add_tab(otra, activate=True)
    window.set_dual("Vertical")
    window.load_named_session("trabajo")
    assert [[v.path for v in g.views()] for g in window.groups[:2]] == expected_paths
    assert window.dual == "Horizontal" and window.groups[0].currentIndex() == 1
    assert window.groups[0].current.tab_name == "Fotos"
    assert window.source is window.groups[window.active_side].current


def test_loading_restores_the_view_mode_and_the_active_pane(window, tmp_path):
    uno, dos = folders(tmp_path, "uno", "dos")
    window.groups[0].current.navigate(uno)
    window.groups[1].current.navigate(dos)
    window.groups[1].current.set_view_mode("details")
    window._view_activated(window.groups[1].current)
    window.save_named_session("modo")
    window._view_activated(window.groups[0].current)
    window.groups[1].current.set_view_mode("list")
    window.load_named_session("modo")
    assert window.active_side == 1 and window.groups[1].current.view_mode == "details"


def test_an_unknown_view_mode_in_a_session_is_ignored(window, tmp_path):
    uno = folders(tmp_path, "uno")[0]
    window.groups[0].current.navigate(uno)
    window.save_named_session("rara")
    window.config["saved_sessions"][0]["data"]["view_modes"] = [["inventada"], [3]]
    window.load_named_session("rara")
    assert window.groups[0].current.path == uno and window.groups[0].current.view_mode != "inventada"


def test_a_saved_tab_whose_folder_no_longer_exists_does_not_stop_the_session(window, tmp_path):
    uno, dos = folders(tmp_path, "uno", "dos")
    window.groups[0].current.navigate(uno)
    window.groups[0].add_tab(dos, activate=True)
    window.save_named_session("borrada")
    window.groups[0].close_all_tabs()
    window.groups[0].add_tab(uno, activate=True)
    os.rmdir(dos)
    window.load_named_session("borrada")
    assert [v.path for v in window.groups[0].views()] == [uno]


def test_session_terminals_come_back_with_the_panes(window, tmp_path, monkeypatch):
    opened = []

    def factory(argv, folder, cols, rows, on_data, on_exit, env=None):
        opened.append(folder)
        return FakeSession(folder)

    for terminal in window.terminals:
        terminal.session_factory = factory
    window._show_terminal(0, True)
    QApplication.processEvents()
    QApplication.processEvents()
    assert window.terminals[0].session is not None
    window.save_named_session("con terminal")
    window._show_terminal(0, False)
    window.load_named_session("con terminal")
    assert not window.terminal_panels[0].isHidden()
    QApplication.processEvents()
    QApplication.processEvents()
    assert len(opened) == 2  # la anterior se cerró y al cargar se abre otra


def test_loading_asks_before_closing_a_running_program(window, tmp_path, monkeypatch):
    uno, dos = folders(tmp_path, "uno", "dos")
    window.groups[0].current.navigate(uno)
    window.save_named_session("una")
    window.groups[0].current.navigate(dos)
    busy = FakeSession(dos, idle=False)
    window.terminals[0].session = busy
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, question, dangerous=False: False)
    window.load_named_session("una")
    assert window.groups[0].current.path == dos and not busy.closed
    window.terminals[0].session = None


def test_the_box_lists_the_sessions_and_choosing_one_loads_it(window, tmp_path):
    uno, dos = folders(tmp_path, "uno", "dos")
    window.groups[0].current.navigate(uno)
    window.save_named_session("una")
    window.groups[0].current.navigate(dos)
    window.save_named_session("otra")
    assert [window.session_box.itemText(row) for row in range(1, window.session_box.count())] == ["una", "otra"]
    window.session_box.setCurrentIndex(1)
    window._session_chosen(1)
    assert window.groups[0].current.path == uno and window._current_session == "una"


def test_the_save_button_proposes_the_default_name(window, monkeypatch):
    asked = {}

    def ask(self, title, label, value=""):
        asked["value"] = value
        return value

    monkeypatch.setattr(MainWindow, "_ask_text", ask)
    window.ask_save_named_session()
    assert asked["value"].startswith("sesion_") and sessions.names(window.config["saved_sessions"]) == [asked["value"]]


def test_overwriting_a_session_needs_confirmation(window, monkeypatch):
    window.save_named_session("una")
    before = window.config["saved_sessions"][0]["data"]
    monkeypatch.setattr(MainWindow, "_ask_choice", lambda self, question, options: 1)
    monkeypatch.setattr(MainWindow, "_ask_text", lambda self, title, label, value="": "una")
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, question, dangerous=False: False)
    window.groups[0].add_tab(window.groups[0].current.path, activate=True)
    window.ask_save_named_session()
    assert window.config["saved_sessions"][0]["data"] is before


def test_the_session_controls_are_at_the_right_of_the_window(window):
    window.resize(1200, 700)
    QApplication.processEvents()
    corner = window.session_box.parentWidget()
    assert corner.mapTo(window, corner.rect().topRight()).x() > window.width() - 20
    assert corner.mapTo(window, corner.rect().topLeft()).y() < 40


def test_preferences_page_renames_and_deletes(qapp, tmp_path, monkeypatch):
    config = Config(str(tmp_path))
    config["saved_sessions"] = sessions.stored(sessions.stored([], "uno", {"tabs": [["a"], ["b", "c"]]}), "dos", {"tabs": []})
    page = SessionsPage(config)
    assert page.list.count() == 2 and "2 paneles" in page.list.item(0).text() and "3 pestañas" in page.list.item(0).text()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    assert not page.rename("uno", "dos") and warnings
    assert page.rename("uno", "tres") and page.current_name() == "tres"
    page.delete_current()
    assert sessions.names(page.values()) == ["dos"]
    assert sessions.names(config["saved_sessions"]) == ["uno", "dos"]  # hasta que se acepten las preferencias


def test_the_box_follows_the_changes_of_the_preferences(window):
    window.save_named_session("una")
    window.config["saved_sessions"] = sessions.renamed(window.config["saved_sessions"], "una", "otra")
    window._preferences_changed()
    assert [window.session_box.itemText(row) for row in range(window.session_box.count())][1:] == ["otra"]


# ------------------------------------------------------------------ git: órdenes
@pytest.mark.parametrize("family,text,expected", [
    (POSIX, "a b", "'a b'"), (POSIX, "a'b", "'a'\"'\"'b'"), (POSIX, "$(rm x)", "'$(rm x)'"),
    (FISH, "a'b\\", "'a\\'b\\\\'"),
    (POWERSHELL, "a b", "'a b'"), (POWERSHELL, "a'b", "'a''b'"), (POWERSHELL, "a\u2019b", "'a\u2019\u2019b'"), (POWERSHELL, "$(x)", "'$(x)'"), (POWERSHELL, 'a"b', None), (POWERSHELL, "a\\", None),
    (CMD, "a b", '"a b"'), (CMD, "a&b", '"a&b"'), (CMD, "a%b", None), (CMD, 'a"b', None), (CMD, "a!b", None), (CMD, "a\\", None),
    (OTHER, "a", None), (POSIX, "a\nb", None), (POWERSHELL, "a\rb", None), (CMD, "a\x1bb", None),
])
def test_quoting_never_leaves_anything_that_could_run(family, text, expected):
    assert git_tools.quote(text, family) == expected


def test_add_command_is_built_with_the_chosen_files():
    assert git_tools.add_command(["a b.txt", "c.txt"], POSIX) == "git add -- 'a b.txt' c.txt"
    assert git_tools.add_command(["a.txt"], CMD) == 'git add -- "a.txt"'
    assert git_tools.add_command([], POSIX) is None
    assert git_tools.add_command(["a%PATH%.txt", "b.txt"], CMD) is None  # uno solo basta para no escribir nada


def test_commit_command_uses_one_option_per_line_and_never_a_line_break():
    assert git_tools.commit_command("Título\n\nDetalle", POWERSHELL) == "git commit -m 'Título' -m 'Detalle'"
    assert git_tools.commit_command("x", CMD, all_changes=True) == 'git commit -a -m "x"'
    assert git_tools.commit_command("", POSIX) is None and git_tools.commit_command("  \n ", POSIX) is None
    assert git_tools.commit_command("", POSIX, amend=True) == "git commit --amend --no-edit"
    assert git_tools.commit_command("y", POSIX, amend=True) == "git commit --amend -m y"
    assert "\n" not in git_tools.commit_command("a\r\nb", POSIX)


def test_switch_command():
    assert git_tools.switch_command("main", POSIX) == "git switch main"
    assert git_tools.switch_command("nueva", POWERSHELL, True) == "git switch -c 'nueva'"
    assert git_tools.switch_command("--detach", POSIX) is None


# ------------------------------------------------------------------ git: el de verdad
GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git no está instalado")


def git(folder, *arguments):
    subprocess.run([GIT, "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@t", *arguments], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    git(folder, "init", "-b", "principal")
    (folder / "viejo.txt").write_text("1", encoding="utf-8")
    (folder / "renombrar.txt").write_text("contenido suficiente\n" * 5, encoding="utf-8")
    git(folder, "add", ".")
    git(folder, "commit", "-m", "inicio")
    return folder


@needs_git
def test_branch_of_a_repository_and_of_a_plain_folder(repo, tmp_path):
    sub = repo / "sub"
    sub.mkdir()
    assert git_tools.current_branch(GIT, str(repo)) == "principal" and git_tools.current_branch(GIT, str(sub)) == "principal"
    plain = tmp_path / "normal"
    plain.mkdir()
    assert git_tools.current_branch(GIT, str(plain)) is None
    assert git_tools.current_branch(GIT, str(tmp_path / "no existe")) is None
    git(repo, "checkout", "--detach")
    assert git_tools.current_branch(GIT, str(repo))  # sin rama, el commit


@needs_git
def test_changes_with_spaces_accents_renames_and_subfolders(repo):
    (repo / "viejo.txt").write_text("2", encoding="utf-8")
    (repo / "con espacio ñ.txt").write_text("x", encoding="utf-8")
    git(repo, "mv", "renombrar.txt", "nuevo nombre.txt")
    sub = repo / "sub"
    sub.mkdir()
    (sub / "dentro.txt").write_text("x", encoding="utf-8")
    found = {change.path: change for change in git_tools.changed_files(GIT, str(repo))}
    assert set(found) == {"viejo.txt", "con espacio ñ.txt", "nuevo nombre.txt", "sub/"}
    assert found["viejo.txt"].can_add and found["con espacio ñ.txt"].state == "??" and not found["nuevo nombre.txt"].can_add
    inside = [change.path for change in git_tools.changed_files(GIT, str(sub))]
    assert "../viejo.txt" in inside and "./" in inside


@needs_git
def test_a_folder_outside_a_repository_reports_the_error(tmp_path):
    with pytest.raises(OSError):
        git_tools.changed_files(GIT, str(tmp_path))


@needs_git
def test_branches_are_listed(repo):
    git(repo, "branch", "otra")
    assert set(git_tools.branches(GIT, str(repo))) == {"principal", "otra"}
    assert git_tools.branches(GIT, str(repo / "no existe")) == []


def test_git_program_missing_is_empty(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert git_tools.git_program() == "" and git_tools.git_program("otro") == ""


# ------------------------------------------------------------------ git: ventanas y barra
def test_add_dialog_builds_the_command_while_checking_files(qapp):
    sent = []
    dialog = FilesDialog([git_tools.Change("a b.txt", " ", "M"), git_tools.Change("c.txt", "?", "?")], POSIX, lambda command: sent.append(command) or True)
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert dialog.preview.text() == "" and not ok.isEnabled()
    dialog.files.item(0).setCheckState(Qt.CheckState.Checked)
    assert dialog.preview.text() == "git add -- 'a b.txt'" and ok.isEnabled()
    dialog.files.item(1).setCheckState(Qt.CheckState.Checked)
    assert dialog.preview.text() == "git add -- 'a b.txt' c.txt"
    dialog.mark_all(False)
    assert not ok.isEnabled()
    dialog.mark_all(True)
    dialog.submit()
    assert sent == ["git add -- 'a b.txt' c.txt"] and dialog.result() == dialog.DialogCode.Accepted


def test_a_busy_terminal_keeps_the_dialog_open_and_says_so(qapp):
    dialog = FilesDialog([git_tools.Change("a", " ", "M")], POSIX, lambda command: False)
    dialog.mark_all(True)
    dialog.submit()
    assert not dialog.notice.isHidden() and dialog.result() != dialog.DialogCode.Accepted


def test_a_file_that_cannot_be_written_safely_disables_accept(qapp):
    dialog = FilesDialog([git_tools.Change("a%b.txt", " ", "M")], CMD, lambda command: True)
    dialog.mark_all(True)
    assert not dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled() and not dialog.notice.isHidden()


def test_commit_dialog_shows_the_command_in_real_time(qapp):
    sent = []
    dialog = CommitDialog([git_tools.Change("a", "M", " ")], POWERSHELL, lambda command: sent.append(command) or True)
    assert dialog.preview.text() == ""
    dialog.message.setPlainText("Arreglo")
    assert dialog.preview.text() == "git commit -m 'Arreglo'"
    dialog.all_changes.setChecked(True)
    dialog.amend.setChecked(True)
    assert dialog.preview.text() == "git commit -a --amend -m 'Arreglo'"
    dialog.submit()
    assert sent == ["git commit -a --amend -m 'Arreglo'"]


class FakeSession:
    def __init__(self, folder="", idle=True):
        self.folder, self.idle, self.closed, self.written = folder, idle, False, []

    def current_directory(self):
        return self.folder

    def shell_is_idle(self):
        return self.idle

    def write(self, data):
        self.written.append(data)

    def resize(self, *args):
        pass

    def close(self):
        self.closed = True


def wait_for(condition, timeout=5000):
    from PySide6.QtCore import QElapsedTimer, QThreadPool

    timer = QElapsedTimer()
    timer.start()
    while not condition() and timer.elapsed() < timeout:
        QApplication.processEvents()
        QThreadPool.globalInstance().waitForDone(5)
    return condition()


def prepared_terminal(window, folder, index=0):
    window._show_terminal(index, True)
    terminal = window.terminals[index]
    terminal.session_factory = lambda *args, **kwargs: FakeSession(folder)
    QApplication.processEvents()
    terminal.dispose()
    terminal.session = FakeSession(folder)
    terminal._family = POSIX
    terminal.screen_.feed_text("$ ")
    terminal._last_activity = 0
    return terminal


@needs_git
def test_the_bar_shows_the_branch_only_inside_a_repository(window, repo, tmp_path):
    prepared_terminal(window, str(repo))
    bar = window.git_bars[0]
    bar.check()
    assert wait_for(lambda: bar.branch == "principal")
    assert not bar.content.isHidden() and "principal" in bar.branch_button.text()
    window.terminals[0].session.folder = str(tmp_path)
    bar.check()
    assert wait_for(lambda: bar.branch == "")
    assert bar.content.isHidden()
    window.terminals[0].session = None


@needs_git
def test_the_bar_notices_a_change_of_branch_made_by_typing(window, repo):
    prepared_terminal(window, str(repo))
    bar = window.git_bars[0]
    bar.check()
    assert wait_for(lambda: bar.branch == "principal")
    git(repo, "switch", "-c", "escrita")
    bar.check()
    assert wait_for(lambda: bar.branch == "escrita")
    window.terminals[0].session = None


@needs_git
def test_preferences_can_turn_it_off_and_hide_the_branch_or_buttons(window, repo):
    prepared_terminal(window, str(repo))
    bar = window.git_bars[0]
    bar.check()
    assert wait_for(lambda: bar.branch == "principal")
    window.config["git_show_branch"] = False
    window.config["bar_buttons"] = {bar_buttons.GIT: ["push"]}
    order = list(reversed(bar_buttons.arranged(window.config, bar_buttons.GIT)))
    window.config["bar_order"] = {bar_buttons.GIT: order}
    bar.apply_settings()
    assert bar.branch_button.isHidden()
    shown = [key for action in bar.tools.actions() for key, known in bar.commands.items() if known is action]
    assert shown == [key for key in order if key != "push"]
    window.config["git_enabled"] = False
    bar.check()
    assert bar.content.isHidden()
    window.terminals[0].session = None


def test_without_git_the_bar_stays_hidden(window, tmp_path, monkeypatch):
    prepared_terminal(window, str(tmp_path))
    bar = window.git_bars[0]
    assert wait_for(lambda: not bar._checking)  # la que haya en marcha aún vería git
    monkeypatch.setattr(shutil, "which", lambda name: None)
    bar.check()
    assert wait_for(lambda: not bar._checking)
    assert bar.content.isHidden() and bar._git == ""
    window.terminals[0].session = None


def test_a_terminal_without_known_syntax_shows_no_bar(window, tmp_path):
    terminal = prepared_terminal(window, str(tmp_path))
    terminal._family = OTHER
    window.git_bars[0].check()
    assert window.git_bars[0].content.isHidden()
    terminal.session = None


def test_a_simple_button_writes_the_command_to_its_own_terminal(window, tmp_path):
    first = prepared_terminal(window, str(tmp_path), 0)
    second = prepared_terminal(window, str(tmp_path), 1)
    window.git_bars[1].commands["status"].trigger()
    assert second.session.written == [b"git status\r"] and first.session.written == []
    first.session = second.session = None


def test_the_command_can_be_left_written_for_review(window, tmp_path):
    terminal = prepared_terminal(window, str(tmp_path))
    window.config["git_run_commands"] = False
    assert window.git_bars[0].send("git status")
    assert terminal.session.written == [b"git status"]
    assert not window.git_bars[0].send("git push")  # ya hay algo escrito: no se añade detrás
    terminal.session = None


@pytest.mark.parametrize("state", ["typing", "alt", "program"])
def test_nothing_is_written_over_what_the_terminal_is_doing(window, tmp_path, state):
    terminal = prepared_terminal(window, str(tmp_path))
    if state == "typing":
        terminal._line_started = True
    elif state == "alt":
        terminal.screen_.alt = True
    else:
        terminal.session.idle = False
    messages = []
    window.git_bars[0]._report = messages.append
    window.git_bars[0].run("push")
    assert terminal.session.written == [] and messages
    terminal.session = None


def test_every_terminal_has_its_bar_also_after_moving_a_pane(window):
    assert len(window.git_bars) == len(window.terminals)
    for bar, terminal in zip(window.git_bars, window.terminals):
        assert bar.terminal is terminal


@needs_git
def test_the_title_of_the_terminal_keeps_its_width_with_the_bar(window, repo):
    prepared_terminal(window, str(repo))
    bar = window.git_bars[0]
    bar.check()
    assert wait_for(lambda: bar.branch == "principal")
    window.resize(1800, 900)
    wait_for(lambda: False, 300)  # el diseño se aplica en varios pasos
    title = bar.parentWidget().layout().itemAt(0).widget()
    assert title.width() >= title.sizeHint().width()
    assert bar.width() > title.width()
    window.terminals[0].session = None


def test_git_bar_can_be_arranged_in_preferences(qapp, tmp_path):
    config = Config(str(tmp_path))
    assert bar_buttons.GIT in [bar for bar, _title, _buttons in bar_buttons.bars(config)]
    assert bar_buttons.arranged(config, bar_buttons.GIT)[:4] == ["status", "add", "unstage", "commit"]


def test_git_page_saves_its_options(qapp, tmp_path):
    config = Config(str(tmp_path))
    page = GitPage(config)
    page.enabled.setChecked(False)
    page.show_branch.setChecked(False)
    page.run_commands.setChecked(False)
    page.program.setText(" C:/git/git.exe ")
    page.apply_to(config)
    assert config["git_enabled"] is False and config["git_show_branch"] is False and config["git_run_commands"] is False and config["git_program"] == "C:/git/git.exe"


# ------------------------------------------------------------------ administrador de archivos: Windows
TEST_ROOT = "Software\\DirectoryCommanderTuxTuxTest"
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="solo en Windows")


@pytest.fixture
def registry():
    """Una rama aparte: el registro que usa Windows no se toca en estas pruebas."""
    created = default_manager.Registry(TEST_ROOT + "\\Classes")
    outer = default_manager.Registry(TEST_ROOT)
    outer.delete_tree("Classes")
    yield created
    outer.delete_tree("Classes")
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_ROOT)
    except OSError:
        pass


def leftovers(registry):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry.root) as handle:
            keys = []
            while True:
                try:
                    keys.append(winreg.EnumKey(handle, len(keys)))
                except OSError:
                    return keys
    except OSError:
        return []


ARGS = ["C:\\Program Files\\App\\app.exe"]


@windows_only
def test_replacing_writes_the_verb_for_folders_and_drives(registry):
    default_manager.replace_explorer(registry, ARGS)
    for cls in ("Directory", "Drive"):
        assert registry.read(f"{cls}\\shell") == default_manager.VERB
        assert registry.read(f"{cls}\\shell\\{default_manager.VERB}\\command") == '"C:\\Program Files\\App\\app.exe" "%1"'
        assert registry.read(f"{cls}\\shell\\{default_manager.EXPLORER_VERB}\\command", "DelegateExecute") == default_manager.EXPLORER_DELEGATE
    assert registry.read("Folder\\shell") is None  # la Papelera y demás siguen en el Explorador
    assert default_manager.windows_state(registry) == (True, False)


@windows_only
def test_restoring_leaves_nothing_when_there_was_nothing(registry):
    default_manager.replace_explorer(registry, ARGS)
    default_manager.set_win_e(registry, ARGS, True)
    assert default_manager.windows_state(registry) == (True, True)
    default_manager.restore_explorer(registry)
    default_manager.set_win_e(registry, ARGS, False)
    assert leftovers(registry) == [] and default_manager.windows_state(registry) == (False, False)


@windows_only
def test_restoring_brings_back_another_program_and_keeps_its_keys(registry):
    registry.write("Directory\\shell", "", "otroprograma")
    registry.write("Directory\\shell\\otroprograma\\command", "", "otro.exe %1")
    default_manager.replace_explorer(registry, ARGS)
    default_manager.replace_explorer(registry, ARGS)  # dos veces: lo que se guarda sigue siendo lo de antes
    assert registry.read("Directory\\shell", default_manager.PREVIOUS_VALUE) == "otroprograma"
    default_manager.restore_explorer(registry)
    assert registry.read("Directory\\shell") == "otroprograma" and registry.read("Directory\\shell\\otroprograma\\command") == "otro.exe %1"
    assert registry.read("Directory\\shell", default_manager.PREVIOUS_VALUE) is None and registry.read(f"Directory\\shell\\{default_manager.VERB}") is None
    assert "Drive" not in leftovers(registry)


@windows_only
def test_win_e_restores_what_there_was(registry):
    key = default_manager.WIN_E_KEY
    registry.write(key, "", "antes.exe")
    registry.write(key, "DelegateExecute", "{antes}")
    default_manager.set_win_e(registry, ARGS, True)
    assert registry.read(key) == '"C:\\Program Files\\App\\app.exe"' and registry.read(key, "DelegateExecute") == ""
    default_manager.set_win_e(registry, ARGS, True)
    default_manager.set_win_e(registry, ARGS, False)
    assert registry.read(key) == "antes.exe" and registry.read(key, "DelegateExecute") == "{antes}"
    assert registry.read(key, default_manager.PREVIOUS_VALUE) is None and registry.read(key, default_manager.PREVIOUS_DELEGATE) is None


@windows_only
def test_win_e_only_removes_what_it_created(registry):
    registry.write("CLSID\\{otra}", "", "x")
    default_manager.set_win_e(registry, ARGS, True)
    default_manager.set_win_e(registry, ARGS, False)
    assert registry.read("CLSID\\{otra}") == "x" and registry.read(default_manager.WIN_E_KEY) is None
    assert registry.read(default_manager.WIN_E_KEY.rsplit("\\", 2)[0] + "\\shell") is None


@windows_only
def test_asking_windows_for_the_handler_changes_nothing_and_answers_text():
    assert isinstance(default_manager.windows_folder_handler(), str)


@pytest.mark.skipif(sys.platform != "win32", reason="el problema de las comillas es de Windows")
@pytest.mark.parametrize("given,expected", [('C:"', "C:\\"), ('C:" ', "C:\\"), ("C:\\", "C:\\"), ('"D:\\a b"', "D:\\a b"), ("C:\\a", "C:\\a"), ("C:", "C:\\")])
def test_a_drive_passed_by_windows_is_understood(given, expected):
    assert default_manager.clean_argument(given) == expected


def test_exceptions_include_what_is_inside(tmp_path):
    base = str(tmp_path / "Aparte")
    assert default_manager.is_exception(base, [base]) and default_manager.is_exception(os.path.join(base, "sub"), [base])
    assert not default_manager.is_exception(base + "2", [base]) and not default_manager.is_exception(str(tmp_path), [base])
    assert not default_manager.is_exception(base, [])


@windows_only
def test_page_only_touches_the_system_when_something_changes(qapp, tmp_path, registry, monkeypatch):
    monkeypatch.setattr(default_manager, "Registry", lambda root=None: registry)
    monkeypatch.setattr(default_manager, "notify_windows", lambda: None)
    config = Config(str(tmp_path))
    page = FileManagerPage(config)
    assert page.off.isChecked() and not page.exceptions.isEnabled() and not page.win_e.isEnabled()
    page.apply_to(config)
    assert leftovers(registry) == []
    page.filesystem.setChecked(True)
    page.win_e.setChecked(True)
    page.apply_to(config)
    assert default_manager.windows_state(registry) == (True, True)
    assert FileManagerPage(config).filesystem.isChecked() and FileManagerPage(config).win_e.isChecked()
    page.off.setChecked(True)
    page.apply_to(config)
    assert leftovers(registry) == [] and FileManagerPage(config).off.isChecked()


@windows_only
def test_page_keeps_the_list_of_exceptions(qapp, tmp_path, registry, monkeypatch):
    monkeypatch.setattr(default_manager, "Registry", lambda root=None: registry)
    monkeypatch.setattr(default_manager, "notify_windows", lambda: None)
    config = Config(str(tmp_path))
    page = FileManagerPage(config)
    page.except_some.setChecked(True)
    assert page.exceptions.isEnabled()
    page.exceptions.addItem("D:\\Aparte")
    page.apply_to(config)
    assert config["file_manager_exceptions"] == ["D:\\Aparte"] and default_manager.windows_state(registry)[0]
    again = FileManagerPage(config)
    assert again.except_some.isChecked() and again.exceptions.count() == 1
    default_manager.restore_explorer(registry)


@windows_only
def test_a_failure_is_reported_without_closing_preferences(qapp, tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("denegado")

    warnings = []
    monkeypatch.setattr(default_manager, "replace_explorer", fail)
    monkeypatch.setattr(default_manager, "notify_windows", lambda: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    config = Config(str(tmp_path))
    page = FileManagerPage(config)
    page.filesystem.setChecked(True)
    page.apply_to(config)
    assert warnings and "denegado" in warnings[0][2]


def test_the_exceptions_open_in_the_explorer_and_not_here(tmp_path, monkeypatch):
    from directory_commander_tux_tux import app

    opened = []
    monkeypatch.setattr(os, "startfile", lambda path, verb: opened.append((path, verb)), raising=False)
    monkeypatch.setattr(app.sys, "platform", "win32")
    config = Config(str(tmp_path))
    config["file_manager_exceptions"] = ["D:\\Aparte"]
    assert app._paths_for_the_explorer(["D:\\Aparte\\sub", "D:\\Otra"], config) == ["D:\\Aparte\\sub"]
    assert opened == [("D:\\Aparte\\sub", "explore")]


# ------------------------------------------------------------------ administrador de archivos: Linux
class FakeXdg:
    """xdg-mime sin el sistema: en Windows no existe y en Linux no se debe cambiar el de verdad."""

    def __init__(self, current="org.kde.dolphin.desktop", fail=False):
        self.current, self.fail, self.calls = current, fail, []

    def __call__(self, arguments):
        self.calls.append(arguments)
        if self.fail:
            return subprocess.CompletedProcess(arguments, 1, b"", b"no")
        if arguments[1] == "query":
            return subprocess.CompletedProcess(arguments, 0, self.current.encode() + b"\n", b"")
        self.current = arguments[2]
        return subprocess.CompletedProcess(arguments, 0, b"", b"")


@pytest.fixture
def xdg_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path


def test_linux_becoming_default_and_going_back(xdg_home):
    xdg = FakeXdg()
    previous = default_manager.become_linux_default(["/opt/app/programa"], xdg)
    assert previous == "org.kde.dolphin.desktop" and xdg.current == default_manager.DESKTOP_FILE
    text = (xdg_home / "data" / "applications" / default_manager.DESKTOP_FILE).read_text(encoding="utf-8")
    assert "Exec=/opt/app/programa %F" in text and f"MimeType={default_manager.DIRECTORY_TYPE};" in text
    assert default_manager.become_linux_default(["/opt/app/programa"], xdg) == ""  # si ya lo era, no se pierde cuál había
    default_manager.restore_linux_default(previous, xdg)
    assert xdg.current == "org.kde.dolphin.desktop"
    assert not (xdg_home / "data" / "applications" / default_manager.DESKTOP_FILE).exists()


def test_linux_a_path_with_spaces_is_quoted_in_the_launcher(xdg_home):
    default_manager.become_linux_default(["/opt/mi app/programa"], FakeXdg())
    assert 'Exec="/opt/mi app/programa" %F' in (xdg_home / "data" / "applications" / default_manager.DESKTOP_FILE).read_text(encoding="utf-8")


def test_linux_failure_is_reported(xdg_home):
    with pytest.raises(OSError):
        default_manager.become_linux_default(["/opt/app"], FakeXdg(fail=True))


def test_linux_without_a_previous_default_removes_our_line(xdg_home):
    folder = xdg_home / "config"
    folder.mkdir()
    mime = folder / "mimeapps.list"
    mime.write_text(f"[Default Applications]\n{default_manager.DIRECTORY_TYPE}={default_manager.DESKTOP_FILE}\ntext/plain=otro.desktop\n", encoding="utf-8")
    default_manager.restore_linux_default("", FakeXdg())
    assert mime.read_text(encoding="utf-8") == "[Default Applications]\ntext/plain=otro.desktop\n"


def test_linux_a_launcher_that_is_not_ours_stays(xdg_home):
    folder = xdg_home / "data" / "applications"
    folder.mkdir(parents=True)
    desktop = folder / default_manager.DESKTOP_FILE
    desktop.write_text("[Desktop Entry]\nName=De un paquete\n", encoding="utf-8")
    default_manager.restore_linux_default("otro.desktop", FakeXdg())
    assert desktop.exists()


def test_linux_page_applies_and_reverts(qapp, tmp_path, xdg_home, monkeypatch):
    xdg = FakeXdg()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(default_manager, "xdg_available", lambda: True)
    monkeypatch.setattr(default_manager, "_run", xdg)
    monkeypatch.setattr(default_manager, "linux_default", lambda run=xdg: FakeXdg.__call__(xdg, ["xdg-mime", "query"]).stdout.decode().strip())
    monkeypatch.setattr(default_manager, "become_linux_default", lambda arguments, run=xdg: become(arguments, xdg))
    monkeypatch.setattr(default_manager, "restore_system_default", lambda previous="": restore(previous, xdg))
    config = Config(str(tmp_path / "c"))
    page = FileManagerPage(config)
    assert not page.default_box.isChecked() and "dolphin" in page.handler.text()
    page.apply_to(config)
    assert xdg.current == "org.kde.dolphin.desktop"
    page.default_box.setChecked(True)
    page.apply_to(config)
    assert xdg.current == default_manager.DESKTOP_FILE and config["file_manager_previous"] == "org.kde.dolphin.desktop"
    page.default_box.setChecked(False)
    page.apply_to(config)
    assert xdg.current == "org.kde.dolphin.desktop"


_become = default_manager.become_linux_default
_restore = default_manager.restore_linux_default


def become(arguments, xdg):
    return _become(arguments, xdg)


def restore(previous, xdg):
    return _restore(previous, xdg)


# ------------------------------------------------------------------ carpetas que llegan desde fuera
SEND_PATHS = """
import json, sys
from PySide6.QtCore import QCoreApplication
from directory_commander_tux_tux.ui.single_instance import send_to_running
app = QCoreApplication([])
sys.exit(0 if send_to_running(sys.argv[1], json.loads(sys.argv[2])) else 1)
"""
SEND_RAW = """
import sys
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalSocket
app = QCoreApplication([])
socket = QLocalSocket()
socket.connectToServer(sys.argv[1])
sys.exit(1) if not socket.waitForConnected(3000) else None
socket.write(sys.argv[2].encode())
socket.flush()
socket.bytesToWrite() == 0 or socket.waitForBytesWritten(3000)
socket.disconnectFromServer()
"""


def from_another_process(script, *arguments):
    """Como en la realidad: el mismo hilo no puede recibir lo que él mismo espera terminar de enviar."""
    process = subprocess.Popen([sys.executable, "-c", script, *arguments], cwd=os.path.dirname(os.path.dirname(__file__)))
    assert wait_for(lambda: process.poll() is not None, 30000)
    return process.returncode


def test_paths_reach_the_running_window(qapp, tmp_path):
    import json

    name = server_name(str(tmp_path))
    received = []
    server = PathServer(name, received.extend)
    assert server.listening
    assert from_another_process(SEND_PATHS, name, json.dumps(["C:\\uno", "D:\\ñandú con espacio"])) == 0
    assert wait_for(lambda: received)
    assert received == ["C:\\uno", "D:\\ñandú con espacio"]
    server.deleteLater()


def test_without_a_window_nothing_is_sent(qapp, tmp_path):
    assert not send_to_running(server_name(str(tmp_path / "nadie")), ["C:\\uno"])


def test_a_second_window_does_not_take_over_from_the_first(qapp, tmp_path):
    name = server_name(str(tmp_path))
    first, second = [], []
    server = PathServer(name, first.extend)
    other = PathServer(name, second.extend)
    assert server.listening and not other.listening
    assert from_another_process(SEND_PATHS, name, '["C:\\\\uno"]') == 0
    assert wait_for(lambda: first)
    assert second == []
    server.deleteLater()
    other.deleteLater()


@pytest.mark.parametrize("data,expected", [("", []), ("no es json", []), ("3", []), ('{"a": 1}', []), ('["a", 3, null]', [["a"]])])
def test_garbage_from_outside_is_ignored(qapp, tmp_path, data, expected):
    name = server_name(str(tmp_path))
    received = []
    server = PathServer(name, received.append)
    assert from_another_process(SEND_RAW, name, data) == 0
    wait_for(lambda: received, 300)
    assert received == expected
    server.deleteLater()


def test_external_paths_open_as_tabs_of_the_active_pane(window, tmp_path):
    uno, dos = folders(tmp_path, "uno", "dos")
    group = window.groups[window.active_side]
    before = group.count()
    window.open_external_paths([uno, str(tmp_path / "no existe"), dos])
    assert group.count() == before + 2 and group.current.path == dos
    assert "no existe" in window.statusBar().currentMessage()


def test_restoring_from_the_command_line_does_not_open_a_window(tmp_path, monkeypatch):
    from directory_commander_tux_tux import app

    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path))
    called = []
    monkeypatch.setattr(default_manager, "restore_system_default", called.append)
    assert app.main(["--restore-file-manager"]) == 0
    assert called == [""]


# ------------------------------------------------------------------ sesiones: guardar, nueva y vista previa
def test_saving_an_open_session_offers_to_overwrite_or_save_as_new(window, monkeypatch):
    window.save_named_session("una")
    uno = window.groups[0].current.path
    window.groups[0].add_tab(uno, activate=True)
    asked = []
    monkeypatch.setattr(MainWindow, "_ask_choice", lambda self, question, options: asked.append(options) or 0)
    assert window.ask_save_named_session()
    assert len(asked[0]) == 2 and len(sessions.find(window.config["saved_sessions"], "una")["tabs"][0]) == window.groups[0].count()
    monkeypatch.setattr(MainWindow, "_ask_choice", lambda self, question, options: 1)
    monkeypatch.setattr(MainWindow, "_ask_text", lambda self, title, label, value="": "dos")
    assert window.ask_save_named_session()
    assert sessions.names(window.config["saved_sessions"]) == ["una", "dos"] and window._current_session == "dos"
    monkeypatch.setattr(MainWindow, "_ask_choice", lambda self, question, options: -1)
    assert not window.ask_save_named_session()


def test_a_loaded_session_has_no_changes_until_something_changes(window):
    window.save_named_session("una")
    window.load_named_session("una")
    assert not window.session_has_changes()
    window.groups[0].add_tab(window.groups[0].current.path, activate=True)
    assert window.session_has_changes()
    window.save_named_session("una")
    assert not window.session_has_changes()


def test_a_new_session_uses_the_default_style_and_forgets_the_name(window, monkeypatch):
    window.apply_style("Horizontal doble panel")
    window.groups[0].add_tab(window.groups[0].current.path, activate=True)
    window.save_named_session("una")
    window.new_session()
    assert window._current_session == "" and window.session_box.currentIndex() == 0
    assert window.config["style"] == "Vertical doble panel" and window.dual == "Vertical" and window.groups[0].count() == 1
    assert sessions.names(window.config["saved_sessions"]) == ["una"] and not window.session_has_changes()


@pytest.mark.parametrize("answer,saved,started", [(0, True, True), (1, False, True), (-1, False, False)])
def test_new_session_asks_about_the_changes(window, monkeypatch, answer, saved, started):
    window.save_named_session("una")
    before = len(sessions.find(window.config["saved_sessions"], "una")["tabs"][0])
    window.groups[0].add_tab(window.groups[0].current.path, activate=True)
    asked = []
    monkeypatch.setattr(MainWindow, "_ask_choice", lambda self, question, options: asked.append(question) or answer)
    window.ask_new_session()
    assert "una" in asked[0]
    assert (len(sessions.find(window.config["saved_sessions"], "una")["tabs"][0]) == before + 1) == saved
    assert (window._current_session == "") == started


def test_new_session_does_not_ask_without_changes(window, monkeypatch):
    window.save_named_session("una")
    monkeypatch.setattr(MainWindow, "_ask_choice", lambda *a: pytest.fail("no había cambios"))
    window.ask_new_session()
    assert window._current_session == ""


def test_an_unnamed_session_with_changes_asks_for_a_name_to_save_it(window, monkeypatch):
    window.new_session()
    window.groups[0].add_tab(window.groups[0].current.path, activate=True)
    monkeypatch.setattr(MainWindow, "_ask_choice", lambda self, question, options: 0)
    monkeypatch.setattr(MainWindow, "_ask_text", lambda self, title, label, value="": None)
    window.ask_new_session()
    assert window.groups[0].count() == 2  # no se dio nombre: se cancela todo
    monkeypatch.setattr(MainWindow, "_ask_text", lambda self, title, label, value="": "guardada")
    window.ask_new_session()
    assert window.groups[0].count() == 1 and sessions.names(window.config["saved_sessions"]) == ["guardada"]


def test_the_open_session_is_remembered_after_closing(window):
    window.save_named_session("una")
    window.save_session()
    assert window.config["session"]["name"] == "una"
    window._current_session = ""
    window.groups[0].close_all_tabs()
    window.groups[1].close_all_tabs()
    window._restore_session([])
    assert window._current_session == "una" and window.session_box.currentData() == "una"


def test_the_session_follows_a_rename_and_a_delete_from_preferences(window, qapp):
    from directory_commander_tux_tux.ui.dialogs import PreferencesDialog

    window.save_named_session("una")
    dialog = PreferencesDialog(window.config, window)
    dialog.sessions_page.rename("una", "otra")
    dialog.apply_to(window.config)
    window._current_session = dialog.sessions_page.followed_name(window._current_session)
    window._preferences_changed()
    assert window._current_session == "otra" and window.session_box.currentData() == "otra"
    window.config["saved_sessions"] = []
    window._preferences_changed()
    assert window._current_session == "" and window.session_box.currentIndex() == 0
    dialog.deleteLater()


def test_preferences_can_open_on_the_sessions_page(window, qapp):
    from directory_commander_tux_tux.ui.dialogs import PreferencesDialog

    dialog = PreferencesDialog(window.config, window)
    dialog.show_sessions()
    assert dialog.pages.currentWidget() is dialog.sessions_page
    dialog.git_page.bar_link.linkActivated.emit("bar")
    assert dialog.pages.currentWidget() is dialog.bar_buttons and dialog.bar_buttons.current_bar == bar_buttons.GIT
    dialog.deleteLater()


def test_the_page_draws_the_chosen_session(qapp, tmp_path):
    config = Config(str(tmp_path))
    data = {"style": "Vertical doble panel", "tabs": [["C:/uno/dos", "C:/tres"], ["C:/cuatro"]], "tab_names": [["", "Mía"], [""]],
            "layout": {"dual": "Vertical", "tree": False, "viewer": True, "meta": False, "terminals": ["first"]}}
    config["saved_sessions"] = sessions.stored([], "una", data)
    page = SessionsPage(config)
    assert page.preview.pane_notes[0] == ["dos", "Mía"] and page.preview.pane_notes[1] == ["cuatro"]
    kinds = {region.kind for region in page.preview.regions}
    assert {styles.PANE, styles.VIEWER, styles.TERMINAL} <= kinds and styles.TREE not in kinds
    page.resize(700, 400)
    assert not page.grab().isNull()
    page.delete_current()
    assert page.preview.isHidden()


def test_old_sessions_are_drawn_from_their_style():
    known = [{"name": "E", "dual": "Off", "tree": False, "viewer": True}]
    layout = sessions.layout_of({"style": "E", "dual": "Horizontal", "pane_terminals": [{"visible": True}], "general_terminal": {"visible": True}}, known)
    assert layout["dual"] == "Horizontal" and layout["viewer"] is True and layout["tree"] is False and layout["terminals"] == [styles.FIRST, styles.GENERAL]


def test_a_snapshot_describes_the_window(window):
    window.apply_style("Vertical doble panel")
    layout = window._session_snapshot()["layout"]
    assert layout["dual"] == "Vertical" and layout["tree"] is True and layout["viewer"] is False


# ------------------------------------------------------------------ selector de carpetas
def test_the_folder_picker_returns_the_checked_folders_and_what_was_written(qapp, tmp_path):
    one, two = tmp_path / "uno", tmp_path / "dos"
    one.mkdir()
    two.mkdir()
    picker = FolderPicker("t", {}, [str(one)])
    assert picker.tabs.count() == 2 and picker.tabs.itemData(1) == str(one)
    assert picker.values() == []
    picker.field.setText(str(two))
    assert picker.values() == [str(two)]
    picker.tree.home_item.setCheckState(0, Qt.CheckState.Checked)
    assert picker.values() == [os.path.normpath(picker.tree.home_item.data(0, 256))]  # con algo marcado el campo no cuenta, como en el original
    picker.reject()


def test_a_row_only_clicked_counts_if_nothing_is_checked(qapp, tmp_path):
    picker = FolderPicker("t", {}, [])
    picker._row_chosen(str(tmp_path))
    assert picker.values() == [str(tmp_path)]
    picker.tree.home_item.setCheckState(0, Qt.CheckState.Checked)
    assert str(tmp_path) not in picker.values()
    picker.reject()


def test_every_row_of_the_picker_shows_its_checkbox(qapp, tmp_path):
    """Qt solo dibuja la casilla de las filas con un estado: comprobar que «se puede marcar» no dice nada, porque de serie todas se pueden."""
    (tmp_path / "hija").mkdir()
    picker = FolderPicker("t", {}, [])
    tree = picker.tree
    rows = [tree.topLevelItem(row) for row in range(tree.topLevelItemCount())]
    assert len(rows) >= 2 and all(item.data(0, Qt.ItemDataRole.CheckStateRole) is not None for item in rows)
    tree._merge(tree.home_item, [("hija", str(tmp_path / "hija"), False, False)])
    child = tree.home_item.child(0)
    assert child.data(0, Qt.ItemDataRole.CheckStateRole) is not None and child.checkState(0) == Qt.CheckState.Unchecked
    picker.reject()


def test_several_rows_can_be_checked_with_the_mouse(qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    picker = FolderPicker("t", {}, [])
    picker.show()
    tree = picker.tree
    QApplication.processEvents()
    chosen = []
    for row in (0, 1):
        item = tree.topLevelItem(row)
        rect = tree.visualItemRect(item)
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(rect.x() + 10, rect.center().y()))
        if item.checkState(0) != Qt.CheckState.Checked:
            QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(rect.x() + 26, rect.center().y()))
        chosen.append(item.checkState(0))
    assert chosen == [Qt.CheckState.Checked, Qt.CheckState.Checked] and len(tree.checked_paths()) == 2
    picker.reject()


def test_the_lists_fill_the_field(qapp, tmp_path):
    config = {"recent": [{"path": str(tmp_path)}, {"path": "::results"}], "favorites": [{"label": "F", "path": str(tmp_path)}]}
    picker = FolderPicker("t", config, [])
    assert picker.recent.count() >= 2 and picker.favorites.count() == 2 and not picker.tabs.isEnabled()
    picker.favorites.setCurrentIndex(1)
    picker._place_chosen(picker.favorites)
    assert picker.field.text() == str(tmp_path) and picker.values() == [str(tmp_path)]
    picker.reject()


def test_a_folder_that_does_not_exist_is_refused_unless_patterns_are_allowed(qapp, tmp_path):
    missing = str(tmp_path / "no")
    picker = FolderPicker("t", {}, [])
    picker.field.setText(missing)
    picker.submit()
    assert picker.chosen == [] and not picker.notice.isHidden() and picker.result() != picker.DialogCode.Accepted
    picker.reject()
    patterns = FolderPicker("t", {}, [], patterns=True)
    patterns.field.setText("*.git")
    patterns.submit()
    assert patterns.chosen == ["*.git"]


@pytest.mark.skipif(sys.platform != "win32", reason="las unidades solo existen en Windows")
def test_the_drives_group_stands_for_the_local_drives(qapp):
    picker = FolderPicker("t", {}, [])
    group = picker.tree.drives_item
    assert group is not None and group.parent() is picker.tree.root_item
    deadline = time.monotonic() + 10
    while picker.tree.root_item.childCount() < 2 and time.monotonic() < deadline:
        qapp.processEvents()
    assert picker.tree.drives_item.parent() is picker.tree.root_item and picker.tree.root_item.child(0) is group
    assert all(picker.tree.root_item.child(row).flags() & Qt.ItemFlag.ItemIsUserCheckable for row in range(picker.tree.root_item.childCount()))
    group.setCheckState(0, Qt.CheckState.Checked)
    assert "C:\\" in picker.tree.checked_paths()
    picker.reject()


def test_the_plus_buttons_use_the_picker(window, monkeypatch, tmp_path):
    used = []
    monkeypatch.setattr(FolderPicker, "ask", classmethod(lambda cls, title, config, tabs, patterns=False, parent=None: used.append((tabs, patterns)) or [str(tmp_path)]))
    panel = window.find_panel
    panel.roots.set_values([])
    panel.excludes.set_values([])
    panel._add_root()
    panel._add_exclude()
    assert panel.roots.values == [str(tmp_path)] and panel.excludes.values == [str(tmp_path)]
    assert used[0][0] == [view.path for view in window.all_views()] and not used[0][1] and used[1][1]


# ------------------------------------------------------------------ git: rama en el panel, usuario y órdenes
def git_repo(path):
    run = lambda *arguments: subprocess.run(["git", "-C", str(path), *arguments], check=True, capture_output=True)
    path.mkdir(exist_ok=True)
    run("init", "-b", "principal")
    run("config", "user.name", "Pruebas Git")
    run("config", "user.email", "p@example.com")
    return run


def wait_until(check, seconds=10):
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        QApplication.processEvents()
    return check()


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="sin git")


@needs_git
def test_folder_branch_reads_the_head_without_git(tmp_path):
    git_repo(tmp_path)
    (tmp_path / "sub").mkdir()
    assert git_tools.folder_branch("git-que-no-existe", str(tmp_path / "sub")) == "principal"


def test_folder_branch_outside_a_repository_and_with_a_detached_head(tmp_path):
    assert git_tools.folder_branch("git", str(tmp_path)) is None
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("0123456789abcdef\n")
    assert git_tools.folder_branch("git", str(repo)) == "0123456"
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/a/b\n")
    assert git_tools.folder_branch("git", str(repo)) == "a/b"


def test_folder_branch_follows_the_link_of_a_worktree(tmp_path):
    real = tmp_path / "real.git" / "worktrees" / "w"
    real.mkdir(parents=True)
    (real / "HEAD").write_text("ref: refs/heads/otra\n")
    work = tmp_path / "w"
    work.mkdir()
    (work / ".git").write_text(f"gitdir: {real}\n")
    assert git_tools.folder_branch("git", str(work)) == "otra"


@needs_git
def test_the_pane_shows_the_branch_of_its_folder(window, tmp_path):
    git_repo(tmp_path)
    view = window.groups[0].current
    window.config["git_program"] = ""
    view.navigate(str(tmp_path))
    assert wait_until(lambda: view.git_branch == "principal" and view.git_label.text().endswith("principal"))
    view.resize(900, 300)
    view._fit_pane_bar()
    assert not view.git_label.isHidden()
    window.config["git_pane_branch"] = False
    window._check_pane_branches()
    assert wait_until(lambda: view.git_branch == "") and view.git_label.isHidden()


def test_the_branch_can_be_hidden_from_the_pane_bar(window, tmp_path):
    view = window.groups[0].current
    view.set_git_branch("principal")
    view.resize(900, 300)
    view._fit_pane_bar()
    assert not view.git_label.isHidden()
    view.set_removed_tools({"gitbranch"})
    assert view.git_label.isHidden()


@needs_git
def test_current_user_of_a_repository(tmp_path):
    git_repo(tmp_path)
    assert git_tools.current_user("git", str(tmp_path)) == ("Pruebas Git", "p@example.com")


def test_the_terminal_bar_shows_the_user(window):
    bar = window.git_bars[0]
    bar.resize(1000, 24)
    bar._show_user("Pruebas Git", "p@example.com")
    assert "Pruebas" in bar.user_label.text() and "Pruebas Git <p@example.com>" in bar.user_label.toolTip()
    bar._show_user("", "")
    assert "sin usuario" in bar.user_label.text()
    window.config["git_show_user"] = False
    bar.apply_settings()
    assert bar.user_label.isHidden()


def test_changed_commands_and_custom_ones_are_cleaned():
    assert git_tools.clean_commands({"status": "git status -sb", "push": "git push", "pull": "a\nb", "otro": "x", "log": "  "}) == {"status": "git status -sb"}
    custom = git_tools.clean_custom([{"key": "custom-1", "label": "", "command": " git stash ", "files": 1}, {"key": "custom-1", "command": "x"}, {"key": "status", "command": "x"}, {"key": "custom-2", "command": "a\nb"}])
    assert custom == [{"key": "custom-1", "label": "git stash", "command": "git stash", "files": False}]
    assert git_tools.new_custom_key(custom) == "custom-2"


def test_command_of_uses_the_preferences():
    config = {"git_commands": {"status": "git status -sb"}, "git_custom_commands": [{"key": "custom-1", "label": "S", "command": "git stash"}]}
    assert git_tools.command_of(config, "status") == "git status -sb"
    assert git_tools.command_of(config, "log") == git_tools.DEFAULT_COMMANDS["log"]
    assert git_tools.command_of(config, "custom-1") == "git stash" and git_tools.command_of(config, "custom-2") == ""


def test_the_bar_runs_edited_and_custom_commands(window):
    bar = window.git_bars[0]
    sent = []
    bar.send = lambda command: sent.append(command) or True
    window.config["git_commands"] = {"status": "git status -sb"}
    window.config["git_custom_commands"] = [{"key": "custom-1", "label": "Guardar", "command": "git stash", "files": False}]
    bar.apply_settings()
    assert bar.commands["status"].toolTip() == "git status -sb" and bar.commands["custom-1"].text() == "Guardar" and bar.commands["custom-1"] in bar.tools.actions()
    bar.commands["status"].trigger()
    bar.commands["custom-1"].trigger()
    assert sent == ["git status -sb", "git stash"]
    window.config["git_custom_commands"] = []
    bar.apply_settings()
    assert "custom-1" not in bar.commands


def test_the_page_edits_commands_and_adds_new_ones(qapp, tmp_path):
    config = Config(str(tmp_path))
    page = BarButtonsPage(config)
    page.show_bar(bar_buttons.GIT)
    editor = page.git_editor
    git_list = page.lists[bar_buttons.GIT]
    keys = [git_list.item(row).data(Qt.ItemDataRole.UserRole) for row in range(git_list.count())]
    assert keys[:4] == ["status", "add", "unstage", "commit"] and not editor.isHidden()
    git_list.setCurrentRow(0)
    assert editor.command.text() == "git status" and not editor.name.isEnabled() and not editor.restore_button.isEnabled()
    editor.command.setText("git status -sb")
    editor.command.textEdited.emit("git status -sb")
    assert editor.restore_button.isEnabled()
    git_list.setCurrentRow(1)
    assert editor.command.text() == "git add --" and not editor.hint.isHidden()
    editor.add()
    assert git_list.currentItem().data(Qt.ItemDataRole.UserRole) == "custom-1" and editor.name.isEnabled()
    editor.name.setText("Guardar")
    editor.name.textEdited.emit("Guardar")
    editor.command.setText("git stash")
    editor.command.textEdited.emit("git stash")
    assert git_list.currentItem().text() == "Guardar"
    page.apply_git_to(config)
    assert config["git_commands"] == {"status": "git status -sb"}
    assert config["git_custom_commands"] == [{"key": "custom-1", "label": "Guardar", "command": "git stash", "files": False}]
    assert bar_buttons.arranged(config, bar_buttons.GIT)[-1] == "custom-1"
    assert page.order_values() == {}  # una orden añadida al final no es un orden distinto
    editor.remove()
    assert git_list.count() == len(keys)
    page.apply_git_to(config)
    assert config["git_custom_commands"] == []
    git_list.setCurrentRow(0)
    editor.restore()
    assert editor.command.text() == "git status"
    page.apply_git_to(config)
    assert config["git_commands"] == {}


def test_only_the_git_bar_shows_the_command_editor(qapp, tmp_path):
    page = BarButtonsPage(Config(str(tmp_path)))
    page.show_bar(bar_buttons.PANE)
    assert page.git_editor.isHidden()
    page.show_bar(bar_buttons.GIT)
    assert not page.git_editor.isHidden()


def test_commands_are_not_imported_from_a_settings_file():
    from directory_commander_tux_tux.core import settings_io

    assert "git_commands" not in settings_io.SETTINGS_TYPES and "git_custom_commands" not in settings_io.SETTINGS_TYPES


# ------------------------------------------------------------------ git: unstage y commit con archivos
def test_unstage_lists_the_two_names_of_a_rename(qapp):
    sent = []
    changes = [git_tools.Change("nuevo", "R", " ", "viejo")]
    dialog = FilesDialog(changes, POSIX, lambda command: sent.append(command) or True, None, git_tools.DEFAULT_COMMANDS["unstage"], True)
    assert "viejo → nuevo" in dialog.files.item(0).text()
    dialog.mark_all(True)
    assert dialog.preview.text() == "git restore --staged -- nuevo viejo"


CHANGES = [git_tools.Change("preparado", "M", " "), git_tools.Change("cambiado", " ", "M"), git_tools.Change("nuevo", "?", "?")]


def commit_dialog(family=POSIX):
    dialog = CommitDialog(CHANGES, family, lambda command: True)
    dialog.message.setPlainText("m")
    return dialog


def test_commit_starts_with_what_is_already_added(qapp):
    dialog = commit_dialog()
    assert [dialog.list.files.item(row).checkState() == Qt.CheckState.Checked for row in range(3)] == [True, False, False]
    assert dialog.preview.text() == "git commit -m m"


def test_commit_names_the_chosen_files_when_they_differ_from_the_added_ones(qapp):
    dialog = commit_dialog()
    dialog.list.files.item(1).setCheckState(Qt.CheckState.Checked)
    assert dialog.preview.text() == "git commit -m m -- preparado cambiado"
    dialog.list.files.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.preview.text() == "git commit -m m -- cambiado"


@pytest.mark.parametrize("family,expected", [(POSIX, "git add -- nuevo && git commit -m m -- preparado nuevo"), (FISH, "git add -- 'nuevo'; and git commit -m 'm' -- 'preparado' 'nuevo'"),
                                             (CMD, 'git add -- "nuevo" && git commit -m "m" -- "preparado" "nuevo"'),
                                             (POWERSHELL, "git add -- 'nuevo'; if ($?) { git commit -m 'm' -- 'preparado' 'nuevo' }")])
def test_commit_adds_first_what_git_does_not_know(qapp, family, expected):
    dialog = commit_dialog(family)
    dialog.list.files.item(2).setCheckState(Qt.CheckState.Checked)
    assert dialog.preview.text() == expected


def test_commit_without_files_cannot_be_accepted_and_says_why(qapp):
    dialog = commit_dialog()
    dialog.list.mark_all(False)
    assert not dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled() and "al menos un archivo" in dialog.notice.text() and not dialog.notice.isHidden()


def test_all_changes_disables_the_list(qapp):
    dialog = commit_dialog()
    dialog.list.mark_all(False)
    dialog.all_changes.setChecked(True)
    assert not dialog.list.isEnabled() and dialog.preview.text() == "git commit -a -m m"
    dialog.all_changes.setChecked(False)
    assert dialog.list.isEnabled()


def test_amend_without_files_only_corrects_the_message(qapp):
    dialog = commit_dialog()
    dialog.amend.setChecked(True)
    assert dialog.preview.text() == "git commit --amend -m m"
    dialog.list.mark_all(False)
    assert dialog.preview.text() == "git commit --only --amend -m m"


def test_a_too_long_command_is_refused(qapp):
    many = [git_tools.Change(f"carpeta/archivo_{number:05}.txt", " ", "M") for number in range(400)]
    dialog = FilesDialog(many, POSIX, lambda command: pytest.fail("no se envía"))
    dialog.mark_all(True)
    assert not dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled() and "demasiados" in dialog.notice.text()
    dialog.submit()


@needs_git
def test_the_commands_really_work_in_git(tmp_path):
    """Lo que arman las ventanas, ejecutado de verdad."""
    run = git_repo(tmp_path)
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text("1")
    no_commits = git_tools.has_commits("git", str(tmp_path))
    assert not no_commits
    run("add", "a.txt")
    assert [change.path for change in git_tools.changed_files("git", str(tmp_path)) if change.staged] == ["a.txt"]
    shell = lambda command: subprocess.run(command, shell=True, cwd=tmp_path, check=True, capture_output=True)
    shell(git_tools.files_command(git_tools.UNSTAGE_WITHOUT_COMMITS, ["a.txt"], CMD if sys.platform == "win32" else POSIX))
    assert not [change for change in git_tools.changed_files("git", str(tmp_path)) if change.staged]
    family = CMD if sys.platform == "win32" else POSIX
    dialog = CommitDialog(git_tools.changed_files("git", str(tmp_path)), family, lambda command: True)
    dialog.message.setPlainText("primero")
    dialog.list.files.item(0).setCheckState(Qt.CheckState.Checked)
    shell(dialog.preview.text())
    assert git_tools.has_commits("git", str(tmp_path))
    assert subprocess.run(["git", "-C", str(tmp_path), "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True).stdout.split() == ["a.txt"]
    (tmp_path / "a.txt").write_text("2")
    run("add", "a.txt", "b.txt")
    staged = [change.path for change in git_tools.changed_files("git", str(tmp_path)) if change.staged]
    assert sorted(staged) == ["a.txt", "b.txt"]
    shell(git_tools.files_command(git_tools.DEFAULT_COMMANDS["unstage"], ["a.txt"], family))
    assert [change.path for change in git_tools.changed_files("git", str(tmp_path)) if change.staged] == ["b.txt"]
    assert (tmp_path / "a.txt").read_text() == "2"  # no se pierde el cambio
    changes = git_tools.changed_files("git", str(tmp_path))
    dialog = CommitDialog(changes, family, lambda command: True)
    dialog.message.setPlainText("segundo")
    for row in range(dialog.list.files.count()):
        item = dialog.list.files.item(row)
        item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole).path in ("a.txt", "c.txt") else Qt.CheckState.Unchecked)
    shell(dialog.preview.text())
    assert sorted(subprocess.run(["git", "-C", str(tmp_path), "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True).stdout.split()) == ["a.txt", "c.txt"]
    assert [change.path for change in git_tools.changed_files("git", str(tmp_path)) if change.staged] == ["b.txt"]  # lo desmarcado se queda añadido, sin entrar


def test_the_user_leaves_room_for_the_buttons(window):
    bar = window.git_bars[0]
    bar._show_user("Un nombre de usuario bastante largo", "p@example.com")
    bar.resize(1000, 24)
    bar._fit_user()  # sin mostrarse no le llegan los cambios de tamaño
    assert not bar.user_label.isHidden() and "Un" in bar.user_label.text() and bar.user_label.text().endswith("…")
    bar.resize(bar.branch_button.sizeHint().width() + 200, 24)
    bar._fit_user()
    assert bar.user_label.text() == "👤" and not bar.user_label.isHidden()
    bar.resize(bar.branch_button.sizeHint().width() + 100, 24)
    bar._fit_user()
    assert bar.user_label.isHidden() and "Un nombre de usuario bastante largo" in bar.user_label.toolTip()
    bar._show_branch("principal")
    assert "Un nombre de usuario bastante largo" in bar.branch_button.toolTip()


# ------------------------------------------------------------------ recordar la sesión
def test_without_remembering_the_session_it_starts_with_the_default_style(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, question, dangerous=False: True)
    config = Config(str(tmp_path))
    config["style"] = "Horizontal doble panel"
    config["session"] = {"tabs": [[str(tmp_path), str(tmp_path)], [str(tmp_path)]], "dual": "Horizontal", "name": "una", "style": "Horizontal doble panel"}
    config["saved_sessions"] = sessions.stored([], "una", {"tabs": [[str(tmp_path)]]})
    config["restore_session"] = False
    window = MainWindow(config, [])
    assert window.dual == "Vertical" and config["style"] == "Vertical doble panel" and window.groups[0].count() == 1 and window._current_session == ""
    window.close()
    window.deleteLater()


def test_remembering_the_session_is_the_default(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, question, dangerous=False: True)
    config = Config(str(tmp_path))
    assert config["restore_session"] is True
    config["session"] = {"tabs": [[str(tmp_path), str(tmp_path)], [str(tmp_path)]], "dual": "Horizontal"}
    window = MainWindow(config, [])
    assert window.dual == "Horizontal" and window.groups[0].count() == 2
    window.close()
    window.deleteLater()


def test_the_page_saves_the_choice(qapp, tmp_path):
    from directory_commander_tux_tux.ui.dialogs import PreferencesDialog

    config = Config(str(tmp_path))
    dialog = PreferencesDialog(config)
    assert dialog.sessions_page.restore.isChecked()
    dialog.sessions_page.restore.setChecked(False)
    dialog.apply_to(config)
    assert config["restore_session"] is False
    dialog.deleteLater()


# ------------------------------------------------------------------ buscar abajo o aparte
def test_the_find_panel_can_live_in_its_own_window(window):
    panel = window.find_panel
    assert window.config["find_panel_place"] == "window"  # es lo predeterminado
    assert window.utility.indexOf(panel) < 0 and panel.window() is window.find_window
    window._set_utility("find")
    assert window.find_window.isVisible() and not panel.isHidden() and window.utility_dock.isHidden()
    window._set_utility("find,toggle")
    assert not window.find_window.isVisible()
    window._set_utility("find")
    window.config["find_panel_place"] = "bottom"
    window._preferences_changed()
    assert not window.find_window.isVisible() and window.utility.indexOf(panel) == 1 and panel.window() is window
    window._set_utility("find")
    assert window.utility.currentWidget() is panel and not window.utility_dock.isHidden()


def test_the_find_window_can_have_one_or_two_columns(window):
    panel = window.find_panel
    assert window.config["find_window_columns"] == 1 and panel.one_column and panel._columns.direction() == panel._columns.Direction.TopToBottom
    window._set_utility("find")
    narrow = window.find_window.width()
    QApplication.processEvents()
    assert panel.modes.mapTo(panel, panel.modes.rect().bottomLeft()).y() < panel.roots.mapTo(panel, panel.roots.rect().topLeft()).y()  # uno encima del otro
    window.config["find_window_columns"] = 2
    window._preferences_changed()
    QApplication.processEvents()
    assert not panel.one_column and panel._columns.direction() == panel._columns.Direction.LeftToRight and window.find_window.width() > narrow
    assert panel.modes.mapTo(panel, panel.modes.rect().topRight()).x() < panel.roots.mapTo(panel, panel.roots.rect().topLeft()).x()  # uno junto al otro
    window.find_window.hide()


def test_the_columns_are_chosen_in_the_window_itself(window):
    panel = window.find_panel
    window._set_utility("find")
    QApplication.processEvents()
    box = panel.columns_box
    assert box.isVisible() and box.currentData() == 1 and panel.one_column
    narrow = window.find_window.width()
    box.setCurrentIndex(box.findData(2))
    box.activated.emit(box.currentIndex())
    QApplication.processEvents()
    assert window.config["find_window_columns"] == 2 and not panel.one_column and window.find_window.width() > narrow
    box.setCurrentIndex(box.findData(1))
    box.activated.emit(box.currentIndex())
    QApplication.processEvents()
    assert window.config["find_window_columns"] == 1 and panel.one_column and window.find_window.width() == narrow
    window.find_window.hide()


def test_the_columns_choice_is_only_in_the_separate_window(window):
    panel = window.find_panel
    window.config["find_panel_place"] = "bottom"
    window._preferences_changed()
    assert panel.columns_box.isHidden()
    window.config["find_panel_place"] = "window"
    window._preferences_changed()
    assert not panel.columns_box.isHidden()


def test_the_sections_have_their_titles(window):
    panel = window.find_panel
    titles = [label.text() for label in panel.findChildren(QLabel) if label.objectName() == "findHeading"]
    assert titles == ["Qué buscar", "Dónde buscar"]


def test_the_separate_window_has_margins(window):
    window._set_utility("find")
    QApplication.processEvents()
    panel = window.find_panel
    corner = panel.mapTo(window.find_window, panel.rect().topLeft())
    assert corner.x() >= 10 and corner.y() >= 10 and window.find_window.width() - panel.width() - corner.x() >= 10
    window.find_window.hide()


def test_the_find_window_grows_when_a_filter_needs_room(window):
    panel = window.find_panel
    window._set_utility("find")
    window.find_window.resize(760, 300)
    QApplication.processEvents()
    grown = []
    panel.needsRoom.connect(grown.append)
    panel.date_filter.op.setCurrentIndex(panel.date_filter.op.findData("between"))
    assert wait_until(lambda: bool(grown), 3)
    assert wait_until(lambda: window.find_window.height() > 300, 3)
    window.find_window.hide()


def test_writing_does_not_change_the_size_of_the_find_window(window):
    panel = window.find_panel
    window._set_utility("find")
    wait_until(lambda: False, 0.2)  # el ajuste de la ventana al abrirse
    window.find_window.resize(760, 300)
    QApplication.processEvents()
    panel.name.box.setEditText("algo")
    wait_until(lambda: False, 0.2)
    assert window.find_window.height() == 300
    window.find_window.hide()


def test_at_the_bottom_it_always_has_two_columns(window):
    panel = window.find_panel
    assert panel.one_column
    window.config["find_panel_place"] = "bottom"
    window._preferences_changed()
    assert not panel.one_column and window.utility.indexOf(panel) == 1
    window.config["find_panel_place"] = "window"
    window._preferences_changed()
    assert panel.one_column and panel.window() is window.find_window


def test_the_find_options_of_the_preferences(qapp, tmp_path):
    from directory_commander_tux_tux.ui.dialogs import PreferencesDialog

    config = Config(str(tmp_path))
    dialog = PreferencesDialog(config)
    assert dialog.find_place.currentData() == "window" and not hasattr(dialog, "find_columns")
    form = dialog.general_terminal_full_width.parentWidget().layout().itemAt(0).layout()
    row_of = lambda widget: form.getWidgetPosition(widget)[0]
    assert row_of(dialog.find_place) > row_of(dialog.general_terminal_full_width)
    dialog.find_place.setCurrentIndex(dialog.find_place.findData("bottom"))
    dialog.apply_to(config)
    assert config["find_panel_place"] == "bottom" and config["find_window_columns"] == 1
    dialog.deleteLater()


def test_the_upgrade_forgets_the_measures_of_the_old_arrangement(tmp_path):
    import json

    (tmp_path / "config.json").write_text(json.dumps({"config_version": 16, "find_window_geometry": "abc", "find_panel_place": "window"}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert config["find_window_geometry"] == "" and config["find_window_columns"] == 1


def test_the_find_window_remembers_where_it_was(window):
    window.config["find_panel_place"] = "window"
    window._place_find_panel()
    window._set_utility("find")
    wait_until(lambda: False, 0.2)  # el ajuste de la ventana al abrirse
    window.find_window.resize(700, 333)
    QApplication.processEvents()
    window.find_window.hide()
    assert window.config["find_window_geometry"]
    again = FindWindow(window.config)
    assert again.height() == 333
    again.deleteLater()


def test_there_is_no_refine_button(window):
    assert not hasattr(window.find_panel, "refine")


# ------------------------------------------------------------------ comparar archivos
def test_compare_button_only_with_two_files(window, tmp_path):
    folder = tmp_path / "comparar"
    folder.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (folder / name).write_text(name)
    (folder / "carpeta").mkdir()
    view = window.groups[0].current
    view.navigate(str(folder))
    assert wait_until(lambda: view.model._loading is None and len(view.model.entries) == 4)
    assert window._compare_bar_actions and not window._compare_bar_actions[0].isEnabled() and not window._compare_menu_action.isEnabled()

    def select(*names):
        selection = view.details.selectionModel()
        selection.clearSelection()
        for row, entry in enumerate(view.model.entries):
            if entry.name in names:
                selection.select(view.model.index(row, 0), selection.SelectionFlag.Select | selection.SelectionFlag.Rows)
        window._update_compare_state()
        return window._compare_bar_actions[0].isEnabled()

    assert not select("a.txt")
    assert select("a.txt", "b.txt") and window.compare_pair() == (str(folder / "a.txt"), str(folder / "b.txt"))
    assert not select("a.txt", "b.txt", "c.txt")
    assert not select("a.txt", "carpeta")
    assert window._compare_menu_action.isEnabled() is False


def test_compare_between_two_panes(window, tmp_path):
    (tmp_path / "izq").mkdir()
    (tmp_path / "der").mkdir()
    (tmp_path / "izq" / "a.txt").write_text("1")
    (tmp_path / "der" / "b.txt").write_text("2")
    left, right = window.groups[0].current, window.groups[1].current
    left.navigate(str(tmp_path / "izq"))
    right.navigate(str(tmp_path / "der"))
    assert wait_until(lambda: len(left.model.entries) == 1 and len(right.model.entries) == 1)
    for view in (left, right):
        view.details.selectionModel().select(view.model.index(0, 0), view.details.selectionModel().SelectionFlag.Select | view.details.selectionModel().SelectionFlag.Rows)
    assert sorted(window.compare_pair()) == sorted([str(tmp_path / "izq" / "a.txt"), str(tmp_path / "der" / "b.txt")])


def test_compare_without_a_pair_says_what_to_do(window):
    window.run_command("Compare FILES")
    assert "dos archivos" in window.statusBar().currentMessage()


def test_old_configs_get_the_compare_button(tmp_path):
    import json

    (tmp_path / "config.json").write_text(json.dumps({"config_version": 13, "toolbar": [{"label": "Duplicados", "commands": ["Set UTILITY=Dupe"]}, {"label": "Otro", "commands": ["Go UP"]}]}), encoding="utf-8")
    labels = [item["label"] for item in Config(str(tmp_path))["toolbar"]]
    assert labels == ["Duplicados", "Comparar archivos", "Otro"]


def test_identical_files(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 3_000_000)
    (tmp_path / "b").write_bytes(b"x" * 3_000_000)
    (tmp_path / "c").write_bytes(b"x" * 2_999_999 + b"y")
    assert file_compare.identical(str(tmp_path / "a"), str(tmp_path / "b"))
    assert not file_compare.identical(str(tmp_path / "a"), str(tmp_path / "c"))
    assert file_compare.first_difference(str(tmp_path / "a"), str(tmp_path / "c")) == 2_999_999
    assert compare_files(str(tmp_path / "a"), str(tmp_path / "b"), ["no se usa"]) == ("identical", None)


def test_a_cancelled_comparison_stops(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 100)
    (tmp_path / "b").write_bytes(b"x" * 100)
    with pytest.raises(InterruptedError):
        file_compare.first_difference(str(tmp_path / "a"), str(tmp_path / "b"), lambda: True)


@pytest.mark.parametrize("program", [pytest.param("real", marks=pytest.mark.skipif(not file_compare.diff_program(), reason="sin diff")), ""])
def test_text_diff_with_and_without_the_program(tmp_path, program):
    (tmp_path / "ñ a.txt").write_text("uno\ndos\ntres\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("uno\nDOS\ntres\ncuatro\n", encoding="utf-8")
    found = file_compare.text_diff(str(tmp_path / "ñ a.txt"), str(tmp_path / "b.txt"), file_compare.diff_program() if program else "")
    assert found.added == 2 and found.removed == 1
    assert "-dos" in found.lines and "+DOS" in found.lines and "+cuatro" in found.lines and found.lines[0].startswith("--- ")


@pytest.mark.parametrize("program", [pytest.param("real", marks=pytest.mark.skipif(not file_compare.diff_program(), reason="sin diff")), ""])
def test_only_line_endings_are_not_a_difference(tmp_path, program):
    (tmp_path / "a.txt").write_bytes(b"uno\ndos\n")
    (tmp_path / "b.txt").write_bytes(b"uno\r\ndos\r\n")
    assert file_compare.text_diff(str(tmp_path / "a.txt"), str(tmp_path / "b.txt"), file_compare.diff_program() if program else "").lines == []


def test_text_in_another_encoding_is_read_as_text(tmp_path):
    (tmp_path / "a.txt").write_bytes("año\n".encode("utf-16"))
    (tmp_path / "b.txt").write_bytes("año\n".encode("cp1252"))
    (tmp_path / "c.bin").write_bytes(b"\x00\x01\x02binario")
    assert file_compare.is_text(str(tmp_path / "a.txt")) and file_compare.is_text(str(tmp_path / "b.txt")) and not file_compare.is_text(str(tmp_path / "c.bin"))
    assert file_compare.text_diff(str(tmp_path / "a.txt"), str(tmp_path / "b.txt")).lines == []


def test_too_big_without_the_program_is_not_a_hang(tmp_path, monkeypatch):
    monkeypatch.setattr(file_compare, "_MAX_PYTHON_DIFF", 10)
    (tmp_path / "a.txt").write_text("a" * 100)
    (tmp_path / "b.txt").write_text("b" * 100)
    assert file_compare.text_diff(str(tmp_path / "a.txt"), str(tmp_path / "b.txt"), "") is None


def test_binary_files_say_where_they_differ(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"\x00\x01\x02\x03")
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\xff\x03")
    assert compare_files(str(tmp_path / "a.bin"), str(tmp_path / "b.bin"), []) == ("binary", 2)


def _image(path, color="#ff0000", size=(40, 30), dots=()):
    image = QImage(size[0], size[1], QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    for x, y in dots:
        image.setPixelColor(x, y, QColor("#00ff00"))
    assert image.save(str(path))
    return str(path)


def test_images_mark_the_exact_pixels_and_area(qapp, tmp_path):
    first, second = _image(tmp_path / "a.png"), _image(tmp_path / "b.png", dots=[(5, 7), (20, 9), (39, 29)])
    kind, found = compare_files(first, second, [])
    assert kind == "images" and found.different == 3 and found.total == 1200 and found.area == (5, 7, 35, 23)
    assert found.marked.pixelColor(5, 7).red() > 200 and found.marked.pixelColor(5, 7).green() < 80


def test_a_tiny_channel_difference_counts(qapp, tmp_path):
    first = _image(tmp_path / "a.png", "#808080")
    image = QImage(first)
    image.setPixelColor(1, 1, QColor("#808081"))
    image.save(str(tmp_path / "b.png"))
    assert compare_images(first, str(tmp_path / "b.png")).different == 1


def test_same_pixels_in_different_files(qapp, tmp_path):
    first = _image(tmp_path / "a.png")
    image = QImage(first)
    image.save(str(tmp_path / "b.bmp"))
    window = CompareWindow(first, str(tmp_path / "b.bmp"))
    kind, found = compare_files(first, str(tmp_path / "b.bmp"), [])
    assert kind == "images" and found.different == 0
    window.show_images(found)
    assert "idénticas" in window.verdict.text()
    window.close()


def test_images_of_different_size_are_compared_scaled(qapp, tmp_path):
    first, second = _image(tmp_path / "a.png"), _image(tmp_path / "b.png", "#0000ff", (80, 60))
    kind, found = compare_files(first, second, [])
    assert kind == "images" and found.second.width() == 80 and found.first.width() == 40 and found.different == 1200  # la misma proporción: se compara
    window = CompareWindow(first, second)
    window.show_images(found)
    assert "distinto tamaño" in window.verdict.text() and window.differences.isEnabled()
    window.close()


def test_images_with_another_shape_keep_it_and_are_not_compared(qapp, tmp_path):
    first, second = _image(tmp_path / "a.png", size=(400, 300)), _image(tmp_path / "b.png", "#0000ff", (300, 400))
    kind, found = compare_files(first, second, [])
    assert kind == "images" and found.marked is None and (found.second.width(), found.second.height()) == (300, 400)
    window = CompareWindow(first, second)
    window.show_images(found)
    assert "distinta proporción" in window.verdict.text() and not window.differences.isEnabled() and len(window.view.panes) == 2
    window.resize(1000, 640)
    window.show()
    QApplication.processEvents()
    pane = window.view.panes[1]
    scale = pane.fit_scale()
    assert scale * 300 / (scale * 400) == pytest.approx(0.75)  # no se deforma: la misma escala en los dos ejes
    assert not window.grab().isNull()
    window.close()


def test_the_differences_column_is_hidden_until_asked(qapp, tmp_path):
    first, second = _image(tmp_path / "a.png"), _image(tmp_path / "b.png", dots=[(1, 1)])
    window = CompareWindow(first, second)
    window.show_images(compare_images(first, second))
    assert window.view.columns[2].isHidden() and not window.differences.isChecked()
    window.differences.setChecked(True)
    assert not window.view.columns[2].isHidden()
    window.differences.setChecked(False)
    assert window.view.columns[2].isHidden()
    window.close()


def test_each_column_shows_the_data_of_its_image(qapp, tmp_path):
    first, second = _image(tmp_path / "a.png"), _image(tmp_path / "b.png", size=(80, 60))
    window = CompareWindow(first, second)
    window.show_images(compare_images(first, second))
    one, other = window.view.detail_labels[0].text(), window.view.detail_labels[1].text()
    assert "40 × 30" in one and "80 × 60" in other and "PNG" in one and "Modificada" in other and "B" in one
    window.close()


def test_identical_pixels_have_nothing_to_show_as_differences(qapp, tmp_path):
    first = _image(tmp_path / "a.png")
    QImage(first).save(str(tmp_path / "b.bmp"))
    window = CompareWindow(first, str(tmp_path / "b.bmp"))
    window.show_images(compare_images(first, str(tmp_path / "b.bmp")))
    assert not window.differences.isEnabled()
    window.close()


def test_an_image_that_cannot_be_read_is_compared_as_a_file(qapp, tmp_path):
    (tmp_path / "a.png").write_bytes(b"\x00no es una imagen")
    (tmp_path / "b.png").write_bytes(b"\x00tampoco")
    assert compare_files(str(tmp_path / "a.png"), str(tmp_path / "b.png"), [])[0] == "binary"


def test_the_external_tool_is_used_only_if_the_files_differ(qapp, tmp_path):
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "b.txt").write_text("2")
    (tmp_path / "c.txt").write_text("1")
    a, b, c = (str(tmp_path / name) for name in ("a.txt", "b.txt", "c.txt"))
    assert compare_files(a, b, ["x", a, b]) == ("external", ["x", a, b])
    assert compare_files(a, c, ["x", a, c])[0] == "identical"


def test_images_stay_internal_unless_asked(qapp, tmp_path):
    first, second = _image(tmp_path / "a.png"), _image(tmp_path / "b.png", "#0000ff")
    assert compare_files(first, second, ["x"])[0] == "images"
    assert compare_files(first, second, ["x"], True)[0] == "external"


def test_detecting_the_comparers(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: {"meld": "/usr/bin/meld", "code": "C:/x/code.cmd"}.get(name))
    monkeypatch.setattr(file_compare, "_found_on_windows", lambda places: "")
    assert [(tool.key, tool.argv) for tool in file_compare.detect_tools()] == [("meld", ("/usr/bin/meld",))]  # un .cmd no: cmd interpretaría el nombre de los archivos


def test_kompare_is_detected_when_installed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/kompare" if name == "kompare" else None)
    assert file_compare.external_arguments({"compare_tool": "kompare"}, "a", "b") == ["/usr/bin/kompare", "a", "b"]


def test_a_tool_that_is_gone_falls_back_to_internal(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(file_compare, "_found_on_windows", lambda places: "")
    assert file_compare.external_arguments({"compare_tool": "kompare"}, "a", "b") == []
    assert file_compare.external_arguments({}, "a", "b") == []


@pytest.mark.parametrize("command,expected", [("prog", ["prog", "a b", "c"]), ("prog --x {b} {a}", ["prog", "--x", "c", "a b"]), ("", []), ('"prog', [])])
def test_custom_command(command, expected):
    assert file_compare.custom_arguments(command, "a b", "c") == expected


def test_compare_page_lists_what_is_installed_and_saves(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(file_compare, "detect_tools", lambda: [file_compare.Tool("meld", "Meld", ("meld",))])
    config = Config(str(tmp_path))
    page = ComparePage(config)
    assert [page.tool.itemData(row) for row in range(page.tool.count())] == ["", "meld", file_compare.CUSTOM] and "Meld" in page.found.text()
    assert page.command.isHidden() and not page.images.isEnabled()
    page.tool.setCurrentIndex(2)
    assert not page.command.isHidden() and page.images.isEnabled()
    page.command.setText("x {a} {b}")
    page.images.setChecked(True)
    page.apply_to(config)
    assert config["compare_tool"] == file_compare.CUSTOM and config["compare_command"] == "x {a} {b}" and config["compare_external_images"] is True


def test_comparer_is_not_imported_from_a_settings_file():
    from directory_commander_tux_tux.core import settings_io

    assert "compare_tool" not in settings_io.SETTINGS_TYPES and "compare_command" not in settings_io.SETTINGS_TYPES


def test_the_window_says_identical_in_green(qapp):
    window = CompareWindow("a", "b")
    window.show_identical()
    assert window.verdict.text() == "Los archivos son idénticos" and "color" in window.verdict.styleSheet()
    assert theme.GIT_COLORS[theme.current().name][0] in window.verdict.styleSheet()
    window.close()


def test_the_window_colors_the_diff(qapp):
    window = CompareWindow("a", "b")
    window.show_text(file_compare.TextDiff(["--- a", "+++ b", "@@ -1 +1 @@", "-uno", "+dos"], 1, 1))
    assert "añadidas: 1" in window.verdict.text() and window.text.toPlainText().endswith("+dos")
    assert not window.grab().isNull()
    window.close()


def test_image_view_zooms_all_panes_together(qapp, tmp_path):
    first, second = _image(tmp_path / "a.png"), _image(tmp_path / "b.png", "#0000ff")
    window = CompareWindow(first, second)
    window.show_images(compare_images(first, second))
    window.resize(900, 500)
    assert not window.grab().isNull()
    view = window.view
    view.zoom(4)
    assert view.zoom_level == 4
    view.pan(QPointF(3, 2))
    assert not window.grab().isNull() and view.offset == QPointF(3, 2)
    view.zoom(2)
    assert view.offset == QPointF(6, 4)
    view.reset_view()
    assert view.zoom_level == 1 and view.offset == QPointF(0, 0)
    view.zoom(0.5)
    assert view.zoom_level == 1
    window.close()


def test_a_real_comparison_opens_its_window(window, tmp_path):
    (tmp_path / "a.txt").write_text("1\n")
    (tmp_path / "b.txt").write_text("2\n")
    window.compare_files(str(tmp_path / "a.txt"), str(tmp_path / "b.txt"), [])
    assert wait_until(lambda: any(isinstance(child, CompareWindow) and child.isVisible() for child in window.children()))
    opened = next(child for child in window.children() if isinstance(child, CompareWindow))
    assert "+2" in opened.text.toPlainText()
    opened.close()


def test_a_comparer_that_cannot_start_falls_back_to_internal(window, tmp_path):
    (tmp_path / "a.txt").write_text("1\n")
    (tmp_path / "b.txt").write_text("2\n")
    window.compare_files(str(tmp_path / "a.txt"), str(tmp_path / "b.txt"), [str(tmp_path / "no_existe.exe")])
    assert wait_until(lambda: any(isinstance(child, CompareWindow) and child.isVisible() for child in window.children()))
    next(child for child in window.children() if isinstance(child, CompareWindow)).close()
