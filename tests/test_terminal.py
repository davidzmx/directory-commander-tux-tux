"""Las terminales integradas: la pantalla, las teclas, los intérpretes reales y su sitio en la ventana."""
import json
import os
import subprocess
import sys
import threading
import time

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from directory_commander_tux_tux.core import bar_buttons
from directory_commander_tux_tux.core import settings_io, shells
from directory_commander_tux_tux.core.config import DEFAULT_TOOLBAR, GENERAL_TERMINAL_COMMAND, TERMINAL_COMMAND, TERMINAL_TOGGLE_COMMAND, Config
from directory_commander_tux_tux.core.pty_session import PtySession, restore_library_path, shell_environment
from directory_commander_tux_tux.core.shells import CMD, CUSTOM, FISH, OTHER, POSIX, POWERSHELL, Shell, available_shells, cd_command, chosen_shell, custom_shell, default_shell, family_of, looks_like_prompt, prompt_folder
from directory_commander_tux_tux.core.vt_screen import Screen, Style
from directory_commander_tux_tux.ui.icons import themed
from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
from directory_commander_tux_tux.ui.main_window import GENERAL_TERMINAL, MainWindow
from directory_commander_tux_tux.ui.terminal import GENERAL_TOGGLE_SHORTCUT, TOGGLE_SHORTCUT, ShellBox, TerminalView, key_text


# ------------------------------------------------------------------ la pantalla
def test_text_wraps_scrolls_and_keeps_history():
    screen = Screen(5, 2)
    screen.feed_text("abcdefg\r\nxy\r\nz")
    assert screen.display() == ["xy", "z"]
    assert ["".join(cell[0] for cell in line) for line in screen.history] == ["abcde", "fg   "]
    assert screen.history[0].wrapped and not screen.history[1].wrapped


def test_writing_the_last_column_waits_to_wrap():
    screen = Screen(3, 2)
    screen.feed_text("abc")
    assert (screen.x, screen.y) == (2, 0)
    screen.feed_text("\rX")
    assert screen.display() == ["Xbc", ""]


def test_utf8_split_between_reads():
    screen = Screen(10, 1)
    word = "ñandú".encode()
    screen.feed(word[:1])
    screen.feed(word[1:])
    assert screen.display() == ["ñandú"]


def test_wide_and_combining_letters_use_their_cells():
    screen = Screen(6, 1)
    screen.feed_text("日a\u0301")
    assert screen.x == 3
    assert screen.lines[0][0][0] == "日" and screen.lines[0][1][0] == "" and screen.lines[0][2][0] == "a\u0301"
    screen.feed_text("\rX")
    assert screen.lines[0][1][0] == " "  # no queda media letra


@pytest.mark.parametrize("sequence, position", [("\x1b[2;3H", (2, 1)), ("\x1b[3;1f\x1b[2C", (2, 2)), ("\x1b[2;2H\x1b[A", (1, 0)), ("\x1b[5G", (4, 0)), ("\x1b[3d", (0, 2))])
def test_cursor_moves(sequence, position):
    screen = Screen(10, 4)
    screen.feed_text(sequence)
    assert (screen.x, screen.y) == position


@pytest.mark.parametrize("sequence, expected", [("\x1b[K", ["ab", "ghijkl"]), ("\x1b[1K", ["   def", "ghijkl"]), ("\x1b[2K", ["", "ghijkl"]),
                                                ("\x1b[J", ["ab", ""]), ("\x1b[2J", ["", ""]), ("\x1b[2P", ["abef", "ghijkl"]),
                                                ("\x1b[2@", ["ab  cd", "ghijkl"]), ("\x1b[2X", ["ab  ef", "ghijkl"])])
def test_erasing(sequence, expected):
    screen = Screen(6, 2)
    screen.feed_text("abcdefghijkl\x1b[1;3H" + sequence)
    assert screen.display() == expected


def test_scroll_region_keeps_the_rest():
    screen = Screen(3, 4)
    screen.feed_text("a\r\nb\r\nc\r\nd\x1b[2;3r\x1b[3;1H\n\nX")
    assert screen.display() == ["a", "", "X", "d"]
    assert not screen.history


def test_insert_and_delete_lines():
    screen = Screen(2, 3)
    screen.feed_text("a\r\nb\r\nc\x1b[2;1H\x1b[L")
    assert screen.display() == ["a", "", "b"]
    screen.feed_text("\x1b[M")
    assert screen.display() == ["a", "b", ""]


def test_colors_and_attributes():
    screen = Screen(8, 1)
    screen.feed_text("\x1b[1;31;44ma\x1b[38;5;200;48;2;1;2;3mb\x1b[38:2::9:8:7mc\x1b[0md\x1b[91;7me")
    styles = [cell[1] for cell in screen.lines[0][:5]]
    assert styles[0] == Style(fg=1, bg=4, bold=True)
    assert styles[1] == Style(fg=200, bg=(1, 2, 3), bold=True)
    assert styles[2].fg == (9, 8, 7)
    assert styles[3] == Style()
    assert styles[4] == Style(fg=9, inverse=True)


def test_erasing_keeps_the_background_color():
    screen = Screen(3, 1)
    screen.feed_text("\x1b[42m\x1b[2K")
    assert screen.lines[0][0][1].bg == 2


def test_full_screen_programs_leave_no_trace():
    screen = Screen(5, 2)
    screen.feed_text("shell\x1b[?1049h\x1b[2J\x1b[Hvim\r\nx\r\ny")
    assert screen.alt and screen.display() == ["x", "y"]
    assert not screen.history
    screen.feed_text("\x1b[?1049l")
    assert not screen.alt and screen.display() == ["shell", ""]
    assert screen.x == 4


def test_answers_questions_about_the_terminal():
    answers = []
    screen = Screen(9, 3, respond=answers.append)
    screen.feed_text("\x1b[2;4H\x1b[6n\x1b[5n\x1b[c")
    assert answers == [b"\x1b[2;4R", b"\x1b[0n", b"\x1b[?1;2c"]


def test_modes_and_messages_that_are_not_text():
    screen = Screen(5, 2)
    screen.feed_text("\x1b[?25l\x1b[?1h\x1b[?2004h\x1b]0;mi título\x07")
    assert not screen.cursor_visible and screen.app_cursor and screen.bracketed_paste
    assert screen.display() == ["", ""]
    screen.feed_text("\x1b]2;otro\x1b\\\x1bP1$r\x1b\\z")
    assert screen.display()[0] == "z"


def test_sequences_can_arrive_in_pieces():
    screen = Screen(5, 1)
    for piece in ("\x1b", "[", "3", "1", "m", "a"):
        screen.feed_text(piece)
    assert screen.lines[0][0] == ("a", Style(fg=1))


def test_line_drawing_charset():
    screen = Screen(5, 1)
    screen.feed_text("\x1b(0qx\x1b(Bq")
    assert screen.display() == ["─│q"]


def test_garbage_does_not_break_the_screen():
    screen = Screen(4, 2)
    screen.feed_text("\x1b[99999;99999H\x1b[99999A\x1b[99999@\x1b[99999M\x1b[?;;;h\x1b[;m\x1b[0;0r\x1b[38;5m\x1b[38;2;1m\x1bZ\xff\x90ok")
    assert (0 <= screen.x < 4) and (0 <= screen.y < 2) and "ok" in "".join(screen.display())


def test_absurd_sequences_are_bounded():
    screen = Screen(4, 2)
    screen.feed_text("a\x1b[999999999999b\x1b[999999999@\x1b[999999999I\x1b[" + "9" * 5000 + "Hz")
    assert all(len(line) == 4 for line in screen.lines)
    assert "z" in "".join(screen.display())


def test_shrinking_keeps_the_cursor_line_and_moves_the_rest_to_history():
    screen = Screen(5, 4)
    screen.feed_text("a\r\nb\r\nc")
    screen.resize(5, 2)
    assert screen.display() == ["b", "c"] and screen.y == 1
    assert ["".join(cell[0] for cell in line).strip() for line in screen.history] == ["a"]


def test_shrinking_removes_empty_rows_first():
    screen = Screen(5, 4)
    screen.feed_text("a")
    screen.resize(5, 2)
    assert screen.display() == ["a", ""] and not screen.history


def test_resize_keeps_every_line_at_the_new_width():
    screen = Screen(5, 2)
    screen.feed_text("abcde")
    screen.resize(3, 3)
    assert screen.display() == ["abc", "", ""] and screen.x == 2
    screen.resize(8, 3)
    screen.feed_text("\x1b[1;8HZ")
    assert all(len(line) == 8 for line in screen.lines)
    assert screen.display()[0] == "abc    Z"


def test_selection_text_joins_wrapped_lines():
    screen = Screen(3, 3)
    screen.feed_text("abcde\r\nfg")
    assert screen.text_between((0, 1), (2, 1)) == "bcde\nf"


def test_old_history_is_discarded_and_row_numbers_stay_valid():
    screen = Screen(3, 1, history=2)
    screen.feed_text("a\r\nb\r\nc\r\nd\r\ne")
    assert screen.dropped == 2 and screen.line_count == 3
    assert screen.text_between((2, 0), (3, 1)) == "c\nd"


# ------------------------------------------------------------------ las teclas
NO_MODIFIER = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.mark.parametrize("key, modifiers, text, expected", [
    (Qt.Key.Key_A, NO_MODIFIER, "a", "a"),
    (Qt.Key.Key_Ntilde, NO_MODIFIER, "ñ", "ñ"),
    (Qt.Key.Key_Return, NO_MODIFIER, "\r", "\r"),
    (Qt.Key.Key_Backspace, NO_MODIFIER, "\b", "\x7f"),
    (Qt.Key.Key_Tab, NO_MODIFIER, "\t", "\t"),
    (Qt.Key.Key_Backtab, SHIFT, "", "\x1b[Z"),
    (Qt.Key.Key_Escape, NO_MODIFIER, "\x1b", "\x1b"),
    (Qt.Key.Key_C, CTRL, "\x03", "\x03"),
    (Qt.Key.Key_Z, CTRL, "\x1a", "\x1a"),
    (Qt.Key.Key_BracketLeft, CTRL, "", "\x1b"),
    (Qt.Key.Key_Up, NO_MODIFIER, "", "\x1b[A"),
    (Qt.Key.Key_Left, CTRL, "", "\x1b[1;5D"),
    (Qt.Key.Key_Home, NO_MODIFIER, "", "\x1b[H"),
    (Qt.Key.Key_Delete, NO_MODIFIER, "\x7f", "\x1b[3~"),
    (Qt.Key.Key_PageUp, NO_MODIFIER, "", "\x1b[5~"),
    (Qt.Key.Key_F1, NO_MODIFIER, "", "\x1bOP"),
    (Qt.Key.Key_F5, NO_MODIFIER, "", "\x1b[15~"),
    (Qt.Key.Key_F12, SHIFT, "", "\x1b[24;2~"),
    (Qt.Key.Key_X, ALT, "x", "\x1bx"),
    (Qt.Key.Key_2, CTRL | ALT, "@", "@"),  # AltGr+2 en un teclado español, tal como lo entrega Windows
    (Qt.Key.Key_Shift, SHIFT, "", ""),
])
def test_keys_become_what_programs_expect(key, modifiers, text, expected):
    assert key_text(key, modifiers, text) == expected


def test_arrows_in_application_mode():
    assert key_text(Qt.Key.Key_Down, NO_MODIFIER, "", app_cursor=True) == "\x1bOB"


# ------------------------------------------------------------------ el intérprete de verdad
def wait_until(condition, seconds: float = 15.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


@pytest.mark.parametrize("path, family, expected", [
    ("C:\\Mis cosas & más", CMD, 'cd /d "C:\\Mis cosas & más"\r'),
    ("C:\\tiene%PATH%", CMD, None),
    ("\\\\servidor\\compartida", CMD, None),
    ("C:\\Mis cosas; rm", POWERSHELL, "Set-Location -LiteralPath 'C:\\Mis cosas; rm'\r"),
    ("C:\\o'brien$(x)", POWERSHELL, "Set-Location -LiteralPath 'C:\\o''brien$(x)'\r"),
    ("C:\\tipo\u2019grafica", POWERSHELL, "Set-Location -LiteralPath 'C:\\tipo\u2019\u2019grafica'\r"),
    ("C:\\tiene%PATH%", POWERSHELL, "Set-Location -LiteralPath 'C:\\tiene%PATH%'\r"),
    ("\\\\servidor\\compartida", POWERSHELL, "Set-Location -LiteralPath '\\\\servidor\\compartida'\r"),
    ("/home/yo/mis cosas", POSIX, " cd '/home/yo/mis cosas'\r"),
    ("/tmp/$(rm -rf ~)", POSIX, " cd '/tmp/$(rm -rf ~)'\r"),
    ("/tmp/o'brien", POSIX, """ cd '/tmp/o'"'"'brien'\r"""),
    ("/tmp/barra\\invertida", POSIX, " cd '/tmp/barra\\invertida'\r"),  # en Linux es una letra más del nombre
    ("/tmp/con\nsalto", POSIX, None),
    ("/tmp/con\x1b[0mescape", POSIX, None),
    ("C:\\Archivos de programa\\Git", POSIX, " cd 'C:/Archivos de programa/Git'\r"),
    ("\\\\servidor\\compartida", POSIX, " cd //servidor/compartida\r"),
    ("C:\\cualquiera", OTHER, None),
    ("/home/yo/mis cosas", FISH, " cd '/home/yo/mis cosas'\r"),
    ("/tmp/o'brien", FISH, " cd '/tmp/o\\'brien'\r"),
    ("/tmp/\\';rm -rf ~;#", FISH, " cd '/tmp/\\\\\\';rm -rf ~;#'\r"),  # con las comillas de bash, en fish esto ejecutaría el rm
    ("/tmp/con\nsalto", FISH, None),
])
def test_cd_never_lets_a_folder_name_run_as_a_command(path, family, expected):
    assert cd_command(path, family) == expected


def prompt_that_fills_the_row(screen, text):
    """Como lo escribe PowerShell: el indicador y, con el cursor ya colocado por su cuenta, la fila siguiente."""
    screen.feed_text(text)
    screen.feed_text("\x1b[2;1H")


def test_a_prompt_that_fills_its_row_is_still_a_prompt():
    screen = Screen(10, 4)
    prompt_that_fills_the_row(screen, "PS /carp> ")
    assert screen.before_cursor() == "" and screen.prompt_text() == "PS /carp>"
    assert screen.line_before_cursor() == "PS /carp>"


def test_the_final_space_of_a_prompt_that_fills_its_row_goes_to_the_next():
    screen = Screen(10, 4)
    screen.feed_text("PS /carpe> ")
    assert (screen.x, screen.y) == (1, 1) and screen.before_cursor() == ""
    assert screen.prompt_text() == "PS /carpe>" and screen.line_before_cursor() == "PS /carpe>"
    assert looks_like_prompt(screen.prompt_text(), POWERSHELL, windows=False)
    assert looks_like_prompt(screen.prompt_text(), POWERSHELL, windows=True)


def test_a_long_prompt_keeps_its_beginning_when_it_fills_the_last_row():
    screen = Screen(10, 4)
    screen.feed_text("PS /carpeta/muy> ")  # se parte en dos filas y la segunda no llena
    assert screen.line_before_cursor() == "PS /carpeta/muy>"
    screen = Screen(10, 4)
    screen.feed_text("PS /carpeta/muy/larga>")
    screen.feed_text("\x1b[3;1H")
    assert screen.before_cursor() == ""


def test_a_short_row_above_is_not_taken_for_the_prompt():
    screen = Screen(10, 4)
    screen.feed_text("corta\r\n")
    assert screen.prompt_text() == "" and screen.line_before_cursor() == ""


def test_output_that_is_not_at_the_start_of_the_row_is_read_as_usual():
    screen = Screen(10, 4)
    screen.feed_text("0123456789")
    screen.feed_text("\x1b[2;1Hab")
    assert screen.prompt_text() == "ab"


def test_a_terminal_with_a_full_row_prompt_is_ready_for_a_command():
    screen = Screen(10, 4)
    prompt_that_fills_the_row(screen, "PS /carp> ")
    assert looks_like_prompt(screen.prompt_text(), POWERSHELL, windows=False)
    assert looks_like_prompt(screen.prompt_text(), POWERSHELL, windows=True)
    assert not looks_like_prompt(screen.before_cursor(), POWERSHELL, windows=False)  # sin esto, se quedaría esperando para siempre


def test_before_cursor_reads_the_prompt():
    screen = Screen(4, 2)
    screen.feed_text("C:\\>  \r\nab>")
    assert screen.before_cursor() == "ab>"
    screen.feed_text("x")
    assert screen.before_cursor() == "ab>x"  # con la última columna escrita, el cursor aún no ha saltado de fila
    screen.feed_text("\x1b[2;4H")
    assert screen.before_cursor() == "ab>x"  # así lo recoloca la consola tras cambiar de tamaño
    screen.feed_text("\x1b[2;3H")
    assert screen.before_cursor() == "ab"


PLAIN_SHELL = next(shell for shell in available_shells() if shell.family != POWERSHELL)  # PowerShell tarda más en arrancar y no le dice su carpeta al sistema


def windows_shells(environ=None, programs=(), files=(), cygwin="", wsl=False):
    found = available_shells(environ or {}, lambda name: f"C:\\bin\\{name}" if name in programs else None, lambda path: path in files, True, lambda: cygwin, lambda: wsl)
    return {shell.key: shell for shell in found}


def test_windows_default_is_powershell():
    assert list(windows_shells(programs=["powershell.exe", "pwsh.exe"])) == [POWERSHELL, "pwsh", CMD]
    found = available_shells({}, lambda name: f"C:\\bin\\{name}" if name == "powershell.exe" else None, lambda _path: False, True, lambda: "", lambda: False)
    assert found[0].key == POWERSHELL and found[0].family == POWERSHELL
    assert chosen_shell(environ={}, which=lambda name: f"C:\\bin\\{name}" if name == "powershell.exe" else None, isfile=lambda _path: False, windows=True, cygwin_root=lambda: "", wsl_installed=lambda: False).key == POWERSHELL


def test_windows_finds_powershell_outside_the_path():
    path = "D:\\Win\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    assert windows_shells({"SystemRoot": "D:\\Win"}, files=[path])[POWERSHELL].argv[0] == path


def test_windows_without_powershell_uses_cmd_from_the_environment():
    assert windows_shells({"COMSPEC": "mi.exe"})[CMD].argv == ["mi.exe"]
    assert list(windows_shells()) == [CMD] and windows_shells()[CMD].argv == ["cmd.exe"]


def test_linux_default_is_bash():
    found = available_shells({"SHELL": "/usr/bin/fish"}, lambda name: f"/usr/bin/{name}" if name in ("fish", "bash", "/usr/bin/fish") else None, windows=False)
    assert found[0].key == "bash"


def test_git_bash_uses_the_real_bash_and_not_its_launcher():
    root = "C:\\Program Files\\Git"
    shell = windows_shells({"ProgramFiles": "C:\\Program Files"}, files=[f"{root}\\usr\\bin\\bash.exe", f"{root}\\bin\\bash.exe"])["gitbash"]
    assert shell.argv == [f"{root}\\usr\\bin\\bash.exe", "--login", "-i"]
    assert shell.family == POSIX and shell.env == {"MSYSTEM": "MINGW64", "CHERE_INVOKING": "1"}


def test_git_bash_is_found_from_git_on_the_path():
    shell = windows_shells(programs=["git.exe"], files=["C:\\usr\\bin\\bash.exe"])["gitbash"]
    assert shell.argv[0] == "C:\\usr\\bin\\bash.exe"


def test_cygwin_comes_from_its_install_folder():
    shell = windows_shells(cygwin="D:\\cyg", files=["D:\\cyg\\bin\\bash.exe"])["cygwin"]
    assert shell.argv == ["D:\\cyg\\bin\\bash.exe", "--login", "-i"] and shell.env == {"CHERE_INVOKING": "1"}
    assert "cygwin" in windows_shells(files=["C:\\cygwin64\\bin\\bash.exe"])
    assert "msys2" in windows_shells(files=["C:\\msys64\\usr\\bin\\bash.exe"])


def test_wsl_needs_a_distribution_and_cannot_follow():
    assert "wsl" not in windows_shells(programs=["wsl.exe"])
    assert windows_shells(programs=["wsl.exe"], wsl=True)["wsl"].family == OTHER


def test_linux_lists_what_is_installed_with_bash_first(monkeypatch):
    found = available_shells({"SHELL": "/usr/bin/zsh"}, lambda name: f"/usr/bin/{name}" if name in ("zsh", "fish", "dash", "bash", "nu", "/usr/bin/zsh") else None, windows=False)
    assert [shell.key for shell in found] == ["bash", "zsh", "fish", "dash", "nu"]
    assert [shell.family for shell in found] == [POSIX, POSIX, FISH, POSIX, OTHER]
    assert found[0].argv == ["/usr/bin/bash"]


def test_linux_adds_the_user_shell_if_it_is_not_a_known_one():
    found = available_shells({"SHELL": "/opt/elvish"}, lambda name: name if name == "/opt/elvish" else "/bin/bash" if name == "bash" else None, windows=False)
    assert [(shell.key, shell.family) for shell in found] == [("bash", POSIX), ("elvish", OTHER)]


def test_linux_without_bash_falls_back_and_without_anything_is_an_error():
    found = available_shells({}, lambda name: name if name == "/bin/sh" else None, windows=False)
    assert found[0].argv == ["/bin/sh"] and found[0].family == POSIX
    assert available_shells({}, lambda _name: None, windows=False) == []
    with pytest.raises(OSError):
        chosen_shell(environ={}, which=lambda _name: None, windows=False)


@pytest.mark.parametrize("program, family", [("cmd.exe", CMD), ("C:\\x\\PowerShell.EXE", POWERSHELL), ("pwsh", POWERSHELL), ("/bin/bash", POSIX), ("C:\\x\\zsh.exe", POSIX), ("nu.exe", OTHER)])
def test_family_comes_from_the_program_name(program, family):
    assert family_of(program) == family


def test_custom_program_with_arguments_and_spaces():
    shell = custom_shell('"C:\\Mis cosas\\bash.exe" --login -i', isfile=lambda _path: False, windows=True)
    assert shell.argv == ["C:\\Mis cosas\\bash.exe", "--login", "-i"] and shell.family == POSIX and shell.env == {"CHERE_INVOKING": "1"}
    assert custom_shell("C:\\Mis cosas\\nu.exe", isfile=lambda _path: True, windows=True).argv == ["C:\\Mis cosas\\nu.exe"]
    assert custom_shell("/opt/mi shell -l", isfile=lambda _path: False, windows=False).argv == ["/opt/mi", "shell", "-l"]


@pytest.mark.parametrize("command", ["", "   ", '"sin cerrar'])
def test_custom_program_that_makes_no_sense_is_an_error(command):
    with pytest.raises(OSError):
        custom_shell(command, isfile=lambda _path: False, windows=False)


def test_chosen_shell_that_is_no_longer_installed_is_an_error():
    with pytest.raises(OSError, match="gitbash"):
        chosen_shell("gitbash", environ={}, which=lambda _name: None, isfile=lambda _path: False, windows=True, cygwin_root=lambda: "", wsl_installed=lambda: False)
    assert chosen_shell(environ={}, which=lambda _name: None, isfile=lambda _path: False, windows=True, cygwin_root=lambda: "", wsl_installed=lambda: False).key == CMD
    assert chosen_shell(CUSTOM, "programa -x").argv[0] == "programa"


@pytest.mark.parametrize("text, family, windows, expected", [
    ("C:\\>", CMD, True, True), ("Presione una tecla . . .", CMD, True, False), ("", CMD, True, False),
    ("PS C:\\>", POWERSHELL, True, True), ("Nombre:", POWERSHELL, True, False),
    ("$", POSIX, True, True), ("usuario@equipo:~$", POSIX, True, True), ("¿Seguro?", POSIX, True, False),
    ("cualquier cosa", POSIX, False, True), ("", POSIX, False, False), ("C:\\>", OTHER, True, False),
    ("yo@equipo ~>", FISH, False, True), ("yo@equipo ~>", FISH, True, True),
])
def test_what_counts_as_a_prompt(text, family, windows, expected):
    assert looks_like_prompt(text, family, windows) is expected


@pytest.mark.parametrize("family, windows, expected", [(CMD, True, True), (POWERSHELL, True, True), (POSIX, True, False), (POSIX, False, False), (POWERSHELL, False, False)])
def test_only_some_shells_clear_the_line_with_escape(family, windows, expected):
    assert shells.can_clear_line_with_escape(family, windows) is expected


@pytest.mark.parametrize("text, expected", [("PS C:\\Mis cosas>", "C:\\Mis cosas"), ("PS C:\\a> dir /b", "C:\\a"), ("PS /home/yo>", "/home/yo"), ("C:\\a>", None), ("❯", None), ("", None)])
def test_powershell_prompt_shows_the_folder(text, expected):
    assert prompt_folder(text) == expected


def test_a_long_line_is_read_from_its_first_row():
    screen = Screen(5, 3)
    screen.feed_text("PS C:\\abcdefgh>")
    assert screen.before_cursor() == "efgh>"
    assert screen.line_before_cursor() == "PS C:\\abcdefgh>"
    screen.feed_text("\r\nuno")
    assert screen.line_before_cursor() == "uno"


def test_shell_environment_restores_the_library_path():
    env = shell_environment({"LD_LIBRARY_PATH": "/empaquetado", "LD_LIBRARY_PATH_ORIG": "/original", "A": "b"})
    assert env["LD_LIBRARY_PATH"] == "/original" and "LD_LIBRARY_PATH_ORIG" not in env
    assert env["A"] == "b" and env["TERM"] == "xterm-256color"
    assert shell_environment({"TERM": "x"}, {"MSYSTEM": "MINGW64", "A": "c"})["MSYSTEM"] == "MINGW64"


def echo_line(text: str) -> bytes:
    return f"echo {text}\r".encode()


def test_real_session_runs_in_the_folder_and_ends(tmp_path):
    folder = tmp_path / "con espacio ñ"
    folder.mkdir()
    screen = Screen(300, 30)  # sin este ancho, la ruta larga de la carpeta se partiría en dos filas
    ended = threading.Event()
    received = threading.Event()

    def on_data(data):
        screen.feed(data)
        received.set()

    session = PtySession(PLAIN_SHELL.argv, str(folder), 300, 30, on_data, ended.set)
    screen.respond = session.write
    try:
        assert received.wait(15)
        session.write(echo_line("hola_terminal"))
        session.write(b"cd\r" if sys.platform == "win32" else b"pwd\r")
        end = time.monotonic() + 15
        while time.monotonic() < end:
            text = "\n".join(screen.display())
            if text.count("hola_terminal") >= 2 and str(folder.name) in text:
                break
            time.sleep(0.05)
        assert "hola_terminal" in text and folder.name in text
        session.write(b"exit\r")
        assert ended.wait(15)
    finally:
        session.close()


def test_real_session_close_ends_a_running_program(tmp_path):
    ended = threading.Event()
    session = PtySession(PLAIN_SHELL.argv, str(tmp_path), 80, 24, lambda _data: None, ended.set)
    session.write(b"ping -t 127.0.0.1\r" if sys.platform == "win32" else b"sleep 60\r")
    time.sleep(0.3)
    started = time.monotonic()
    session.close()
    assert time.monotonic() - started < 5


def test_real_session_is_busy_while_a_program_runs(tmp_path):
    session = PtySession(PLAIN_SHELL.argv, str(tmp_path), 80, 24, lambda _data: None, lambda: None)
    try:
        time.sleep(1)
        assert session.shell_is_idle()
        session.write(b"ping -n 4 127.0.0.1\r" if sys.platform == "win32" else b"sleep 3\r")
        end = time.monotonic() + 5
        while session.shell_is_idle() and time.monotonic() < end:
            time.sleep(0.02)
        assert not session.shell_is_idle()
        end = time.monotonic() + 15
        while not session.shell_is_idle() and time.monotonic() < end:
            time.sleep(0.05)
        assert session.shell_is_idle()
    finally:
        session.close()
    assert not session.shell_is_idle()


@pytest.mark.skipif(sys.platform != "win32", reason="en Windows el número de un padre muerto se reutiliza")
def test_an_orphan_older_than_the_shell_is_not_its_child(tmp_path):
    """Un programa cuyo padre ya murió conserva el número de este. Si el intérprete lo hereda, no por eso está ocupado."""
    code = "import subprocess; print(subprocess.Popen(['ping', '-n', '30', '127.0.0.1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid)"
    parent = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    orphan = int(parent.stdout)
    parent_id = next(entry for entry in _processes() if entry[0] == orphan)[1]
    session = PtySession(PLAIN_SHELL.argv, str(tmp_path), 80, 24, lambda _data: None, lambda: None)
    try:
        time.sleep(1)
        session._console._process_id = parent_id
        assert session.shell_is_idle()
    finally:
        session.close()
        os.kill(orphan, 9)


def _processes() -> list[tuple[int, int]]:
    """Cada proceso con el número de su padre, tal como lo da Windows."""
    import ctypes

    from directory_commander_tux_tux.core import pty_session
    snapshot = pty_session._CreateToolhelp32Snapshot(pty_session._TH32CS_SNAPPROCESS, 0)
    entry = pty_session._ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    rows = []
    found = pty_session._Process32FirstW(snapshot, ctypes.byref(entry))
    while found:
        rows.append((entry.th32ProcessID, entry.th32ParentProcessID))
        found = pty_session._Process32NextW(snapshot, ctypes.byref(entry))
    pty_session._CloseHandle(snapshot)
    return rows


def test_missing_folder_is_an_error_not_a_crash(tmp_path):
    with pytest.raises(OSError):
        PtySession(PLAIN_SHELL.argv, str(tmp_path / "no existe"), 80, 24, lambda _data: None, lambda: None)


def test_real_session_knows_its_folder_after_cd(tmp_path):
    inside = tmp_path / "dentro ñ"
    inside.mkdir()
    session = PtySession(PLAIN_SHELL.argv, str(tmp_path), 200, 10, lambda _data: None, lambda: None)
    try:
        end = time.monotonic() + 5
        while time.monotonic() < end and session.current_directory() is None:  # recién arrancado, puede no haberla escrito aún
            time.sleep(0.05)
        assert same(session.current_directory(), tmp_path)
        session.write(b'cd "dentro \xc3\xb1"\r')
        end = time.monotonic() + 15
        while time.monotonic() < end and not same(session.current_directory(), inside):
            time.sleep(0.05)
        assert same(session.current_directory(), inside)
    finally:
        session.close()


def test_real_session_receives_the_extra_environment(tmp_path):
    screen = Screen(200, 10)
    session = PtySession(PLAIN_SHELL.argv, str(tmp_path), 200, 10, screen.feed, lambda: None, {"VARIABLE_DE_PRUEBA": "valor_recibido"})
    try:
        session.write(b"echo %VARIABLE_DE_PRUEBA%\r" if sys.platform == "win32" else b"echo $VARIABLE_DE_PRUEBA\r")
        end = time.monotonic() + 15
        while time.monotonic() < end and not any(line.startswith("valor_recibido") for line in screen.display()):
            time.sleep(0.05)
        assert any(line.startswith("valor_recibido") for line in screen.display())
    finally:
        session.close()


# ------------------------------------------------------------------ en la ventana
class FakeSession:
    """Sin intérprete: basta para probar dónde se coloca la terminal y qué le llega."""
    instances: list = []

    def __init__(self, argv, cwd, cols, rows, on_data, on_exit, env=None) -> None:
        self.argv, self.cwd, self.size, self.on_data, self.on_exit, self.env = argv, cwd, (cols, rows), on_data, on_exit, env
        self.sent = b""
        self.closed = False
        self.idle = True
        self.directory = None
        FakeSession.instances.append(self)

    def shell_is_idle(self) -> bool:
        return self.idle

    def current_directory(self):
        return self.directory

    def write(self, data: bytes) -> None:
        self.sent += data

    def resize(self, cols: int, rows: int) -> None:
        self.size = (cols, rows)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def qapp_offscreen():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def folders(tmp_path):
    left, right = tmp_path / "izquierda", tmp_path / "derecha"
    left.mkdir()
    right.mkdir()
    return left, right


@pytest.fixture
def fake_sessions(monkeypatch):
    FakeSession.instances = []
    monkeypatch.setattr(TerminalView, "session_factory", FakeSession)
    return FakeSession.instances


@pytest.fixture
def window(qapp_offscreen, tmp_path, monkeypatch, folders, fake_sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    main = MainWindow(Config(), [str(folder) for folder in folders])
    main.resize(1500, 800)
    main.show()
    QApplication.processEvents()
    yield main
    main.close()
    main.deleteLater()
    QApplication.processEvents()


def same(a, b) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def settle(milliseconds: int = 30) -> None:
    QTest.qWait(milliseconds)


def press_pane_button(window, side: int) -> None:
    view = window.groups[side].current
    button = next(button for button, icon in view._tool_buttons if icon == "terminalpane")
    button.click()
    settle()


def test_each_pane_button_opens_the_terminal_of_that_pane_in_its_folder(window, folders, fake_sessions):
    press_pane_button(window, 1)
    assert window.active_side == 1
    assert not window.terminal_panels[1].isHidden() and window.terminal_panels[0].isHidden()
    assert len(fake_sessions) == 1 and same(fake_sessions[0].cwd, folders[1])
    press_pane_button(window, 0)
    assert window.active_side == 0
    assert not window.terminal_panels[0].isHidden()
    assert same(fake_sessions[1].cwd, folders[0])


def test_terminal_goes_below_in_vertical_and_to_the_right_in_horizontal(window):
    window.apply_style("Vertical doble panel")
    press_pane_button(window, 0)
    group, panel = window.groups[0], window.terminal_panels[0]
    assert window.pane_areas[0].orientation() == Qt.Orientation.Vertical
    assert panel.geometry().top() >= group.geometry().bottom() and panel.width() == group.width()
    assert group.height() > panel.height() >= 160

    window.apply_style("Horizontal doble panel")
    settle()
    assert window.pane_areas[0].orientation() == Qt.Orientation.Horizontal
    assert panel.geometry().left() >= group.geometry().right() and panel.height() == group.height()
    assert abs(group.width() - panel.width()) < 20

    window.apply_style("Vertical doble árbol")
    settle()
    assert window.pane_areas[0].orientation() == Qt.Orientation.Vertical
    assert panel.geometry().top() >= group.geometry().bottom()


def test_terminal_is_below_with_a_single_pane(window):
    window.apply_style("Explorador")
    settle()
    window.run_command(TERMINAL_TOGGLE_COMMAND)
    settle()
    assert window.pane_areas[0].orientation() == Qt.Orientation.Vertical
    assert not window.terminal_panels[0].isHidden()


def test_double_tree_keeps_its_tree_beside_the_terminal(window):
    window.apply_style("Vertical doble árbol")
    press_pane_button(window, 0)
    assert window.sides[0].widget(0) is window.tree_panels[0] and window.sides[0].widget(1) is window.pane_areas[0]
    assert window.tree_panels[0].height() >= window.terminal_panels[0].height() + window.groups[0].height()


def toolbar_action(window):
    return next(action for action in window.toolbar.actions() if action.text() == "Terminal general")


def press_general(window) -> None:
    toolbar_action(window).trigger()
    settle()


def test_toolbar_button_is_next_to_open_terminal_and_opens_the_general_terminal(window, fake_sessions):
    commands = [item["commands"][0] for item in DEFAULT_TOOLBAR if item.get("commands")]
    assert commands.index(GENERAL_TERMINAL_COMMAND) == commands.index(TERMINAL_COMMAND) + 1
    assert TERMINAL_TOGGLE_COMMAND not in commands
    action = toolbar_action(window)
    assert action.isEnabled() and "Ctrl+Mayús+G" in action.toolTip()
    window.groups[1].current.activated.emit(window.groups[1].current)
    press_general(window)
    general = window.terminal_panels[GENERAL_TERMINAL]
    assert not general.isHidden() and window.terminal_panels[0].isHidden() and window.terminal_panels[1].isHidden()
    assert window.focusWidget() is window.terminals[GENERAL_TERMINAL]
    press_general(window)
    assert general.isHidden()
    assert len(fake_sessions) == 1 and not fake_sessions[0].closed


def test_general_terminal_is_below_both_panes_in_vertical(window):
    window.apply_style("Vertical doble panel")
    press_general(window)
    panel, left, right = window.terminal_panels[GENERAL_TERMINAL], window.groups[0], window.groups[1]
    assert window.work_area.orientation() == Qt.Orientation.Vertical
    top = panel.mapTo(window, QPoint(0, 0))
    assert top.y() >= left.mapTo(window, QPoint(0, left.height())).y()
    assert top.y() >= right.mapTo(window, QPoint(0, right.height())).y()
    assert top.x() <= left.mapTo(window, QPoint(0, 0)).x()
    assert top.x() + panel.width() >= right.mapTo(window, QPoint(right.width(), 0)).x()  # ocupa el ancho de los dos
    assert window.splitter.height() > panel.height() >= 160


def test_general_terminal_is_right_of_both_panes_in_horizontal(window):
    press_general(window)
    window.apply_style("Horizontal doble panel")
    settle()
    panel, top, bottom = window.terminal_panels[GENERAL_TERMINAL], window.groups[0], window.groups[1]
    assert window.work_area.orientation() == Qt.Orientation.Horizontal
    corner = panel.mapTo(window, QPoint(0, 0))
    assert corner.x() >= top.mapTo(window, QPoint(top.width(), 0)).x()
    assert corner.x() >= bottom.mapTo(window, QPoint(bottom.width(), 0)).x()
    assert corner.y() <= top.mapTo(window, QPoint(0, 0)).y()
    assert corner.y() + panel.height() >= bottom.mapTo(window, QPoint(0, bottom.height())).y()  # ocupa el alto de los dos
    assert abs(window.splitter.width() - panel.width()) < 20

    window.apply_style("Vertical doble panel")
    settle()
    assert window.work_area.orientation() == Qt.Orientation.Vertical
    assert panel.mapTo(window, QPoint(0, 0)).y() >= top.mapTo(window, QPoint(0, top.height())).y()


def test_general_terminal_is_below_with_a_single_pane(window):
    window.apply_style("Explorador")
    settle()
    press_general(window)
    assert window.work_area.orientation() == Qt.Orientation.Vertical
    assert not window.terminal_panels[GENERAL_TERMINAL].isHidden()


def test_general_and_pane_terminals_live_side_by_side(window, fake_sessions):
    press_general(window)
    press_pane_button(window, 0)
    press_pane_button(window, 1)
    assert len(fake_sessions) == 3
    assert all(not window.terminal_panels[index].isHidden() for index in (0, 1, GENERAL_TERMINAL))


def test_general_terminal_starts_in_the_active_pane_and_does_not_change_it(window, folders, fake_sessions):
    window.groups[1].current.activated.emit(window.groups[1].current)
    press_general(window)
    assert same(fake_sessions[0].cwd, folders[1])
    window.terminals[GENERAL_TERMINAL].focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
    assert window.active_side == 1


def test_general_terminal_follows_the_active_pane(window, folders, tmp_path):
    press_general(window)
    terminal, session = window.terminals[GENERAL_TERMINAL], FakeSession.instances[-1]
    terminal.screen_.feed_text("C:\\>")
    terminal._last_activity = 0
    window.groups[1].current.activated.emit(window.groups[1].current)
    assert wait_until(lambda: session.sent)
    assert session.sent == cd_command(str(folders[1])).encode()
    session.sent = b""
    terminal._last_activity = 0
    other = tmp_path / "otra"
    other.mkdir()
    window.groups[0].current.navigate(str(other))  # el panel que no está activo
    QTest.qWait(50)
    assert session.sent == b""
    window.groups[1].current.navigate(str(other))
    assert wait_until(lambda: session.sent)
    assert session.sent == cd_command(str(other)).encode()


def test_exit_hides_the_general_terminal_and_focus_returns_to_the_list(window, fake_sessions):
    press_general(window)
    fake_sessions[0].on_exit()
    settle()
    assert window.terminal_panels[GENERAL_TERMINAL].isHidden() and window.terminals[GENERAL_TERMINAL].session is None
    assert window.focusWidget() is not window.terminals[GENERAL_TERMINAL]


def test_general_shortcut_goes_to_the_window(window):
    from PySide6.QtGui import QKeyEvent
    terminal, _session = open_terminal(window)
    event = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_G, CTRL | SHIFT)
    event.ignore()
    terminal.event(event)
    assert not event.isAccepted()
    actions = [action for menu in window.main_menu.findChildren(type(window.favorites_menu)) for action in menu.actions()]
    assert next(action for action in actions if action.text() == "Terminal general").shortcut().toString() == GENERAL_TOGGLE_SHORTCUT


# ------------------------------------------------------------------ el botón de seguir al panel
def follow_button(window, index: int = 0):
    return window.terminal_follow_buttons[index]


def test_follow_button_is_on_by_default_in_every_terminal(window):
    assert all(button.isCheckable() and button.isChecked() and button.isEnabled() for button in window.terminal_follow_buttons)
    assert len(window.terminal_follow_buttons) == GENERAL_TERMINAL + 1
    assert follow_button(window, 0).toolTip() == "Seguir a este panel"
    assert follow_button(window, GENERAL_TERMINAL).toolTip() == "Seguir al panel activo"
    assert all(button.parent().parent() is panel for button, panel in zip(window.terminal_follow_buttons, window.terminal_panels))


def test_turning_it_off_stops_following(window, folders):
    terminal, session = ready_fake(window)
    follow_button(window).click()
    assert not terminal.following and not follow_button(window).isChecked()
    window.groups[0].current.navigate(str(folders[1]))
    QTest.qWait(320)
    assert session.sent == b"" and terminal._wanted_folder is None


def test_turning_it_off_cancels_a_change_that_was_waiting(window, folders):
    terminal, session = ready_fake(window)
    session.idle = False
    window.groups[0].current.navigate(str(folders[1]))
    assert terminal._wanted_folder == str(folders[1])
    follow_button(window).click()
    session.idle = True
    QTest.qWait(320)
    assert session.sent == b"" and terminal._wanted_folder is None and not terminal._follow_timer.isActive()


def test_turning_it_on_goes_to_the_pane_folder_even_if_it_looks_the_same(window, folders):
    terminal, session = ready_fake(window)
    follow_button(window).click()
    follow_button(window).click()  # entretanto el usuario ha podido moverse con cd
    assert terminal.following and follow_button(window).isChecked()
    assert wait_until(lambda: session.sent)
    assert session.sent == cd_command(str(folders[0])).encode()


def test_each_terminal_has_its_own_follow_state(window, folders):
    terminal, session = ready_fake(window)
    follow_button(window, 1).click()
    assert terminal.following and not window.terminals[1].following
    window.groups[0].current.navigate(str(folders[1]))
    assert wait_until(lambda: session.sent)


def test_terminal_that_cannot_follow_disables_the_button(window, monkeypatch, folders):
    monkeypatch.setattr(window, "_chosen_shell", lambda: Shell("wsl", "WSL", ["wsl.exe"], OTHER))
    window.terminals[0].shell = window._chosen_shell
    press_pane_button(window, 0)
    terminal, button = window.terminals[0], follow_button(window)
    assert not button.isEnabled() and not button.isChecked() and button.toolTip() == "Esta terminal no puede seguir al panel"
    session = FakeSession.instances[-1]
    terminal.screen_.feed_text("C:\\>")
    terminal._last_activity = 0
    window.groups[0].current.navigate(str(folders[1]))
    QTest.qWait(320)
    assert session.sent == b"" and terminal._wanted_folder is None


# ------------------------------------------------------------------ la lista del intérprete de cada terminal
CMD_SHELL = Shell(CMD, "cmd", ["cmd.exe"], CMD)
BASH_SHELL = Shell("gitbash", "Git Bash", ["bash.exe", "-i"], POSIX, {"MSYSTEM": "MINGW64"})
PS_SHELL = Shell(POWERSHELL, "PowerShell", ["powershell.exe"], POWERSHELL)


@pytest.fixture
def three_shells(monkeypatch):
    monkeypatch.setattr(shells, "available_shells", lambda **_search: [CMD_SHELL, BASH_SHELL, PS_SHELL])


def choose(window, index: int, key: str) -> None:
    box = window.terminal_shell_boxes[index]
    row = box.findData(key)
    box.setCurrentIndex(row)
    box.activated.emit(row)
    settle()


def wait_closed(session) -> bool:
    return wait_until(lambda: session.closed, 5)


def test_every_terminal_has_its_list_showing_what_runs(window, three_shells):
    press_general(window)
    press_pane_button(window, 0)
    for index in (0, GENERAL_TERMINAL):
        box = window.terminal_shell_boxes[index]
        assert [box.itemData(row) for row in range(box.count())] == [CMD, "gitbash", POWERSHELL] and box.currentData() == CMD
        assert box.parent().parent() is window.terminal_panels[index]
        assert box.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_choosing_another_shell_opens_it_in_the_folder_where_the_terminal_is(window, three_shells, fake_sessions, folders, tmp_path):
    press_pane_button(window, 0)
    old = fake_sessions[0]
    old.directory = str(tmp_path)  # el usuario se movió con cd, y el panel sigue donde estaba
    window.terminals[0].screen_.feed_text("texto de antes")
    choose(window, 0, "gitbash")
    new = fake_sessions[1]
    assert same(new.cwd, tmp_path) and new.argv == BASH_SHELL.argv and new.env == BASH_SHELL.env
    assert wait_closed(old) and not new.closed
    terminal = window.terminals[0]
    assert terminal.session is new and terminal.running_shell == BASH_SHELL and same(terminal._current_folder, tmp_path)
    assert "texto de antes" not in "".join(terminal.screen_.display()) and window.focusWidget() is terminal
    assert not window.terminal_panels[0].isHidden() and window.terminal_shell_boxes[0].currentData() == "gitbash"
    assert new.sent == b""  # no salta a la carpeta del panel hasta que este cambie


def test_following_after_changing_uses_the_new_shell(window, three_shells, fake_sessions, folders):
    press_pane_button(window, 0)
    choose(window, 0, POWERSHELL)
    terminal, session = window.terminals[0], fake_sessions[1]
    terminal.screen_.feed_text("PS C:\\> ")
    terminal._last_activity = 0
    window.groups[0].current.navigate(str(folders[1]))
    assert wait_until(lambda: session.sent)
    assert session.sent == cd_command(str(folders[1]), POWERSHELL).encode()


def test_powershell_folder_is_read_from_its_prompt(window, three_shells, fake_sessions, folders, tmp_path):
    press_pane_button(window, 0)
    choose(window, 0, POWERSHELL)
    terminal = window.terminals[0]
    fake_sessions[1].directory = str(folders[1])  # lo que tiene apuntado el sistema: PowerShell no lo actualiza
    terminal.screen_.feed_text(f"PS {tmp_path}> ")
    assert same(terminal.current_path(), tmp_path)


def test_without_knowing_the_folder_it_uses_the_last_one_it_went_to(window, three_shells, fake_sessions, folders, tmp_path):
    press_pane_button(window, 0)
    terminal = window.terminals[0]
    fake_sessions[0].directory = str(tmp_path / "ya no existe")
    assert same(terminal.current_path(), folders[0])
    choose(window, 0, POWERSHELL)
    terminal.screen_.feed_text("mi indicador propio $ ")
    assert same(terminal.current_path(), folders[0])


def test_clicking_the_list_opens_it_and_changes_the_shell(window, three_shells, fake_sessions):
    press_pane_button(window, 0)
    box = window.terminal_shell_boxes[0]
    QTest.mouseClick(box, Qt.MouseButton.LeftButton)
    settle(QApplication.doubleClickInterval() + 50)  # hasta entonces la lista no atiende: sería el doble clic que la abrió
    view = box.view()
    assert view.isVisible()
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(box.model().index(box.findData("gitbash"), 0)).center())
    settle()
    assert len(fake_sessions) == 2 and fake_sessions[1].argv == BASH_SHELL.argv and box.currentData() == "gitbash"


def test_choosing_the_same_shell_does_nothing(window, three_shells, fake_sessions):
    press_pane_button(window, 0)
    choose(window, 0, CMD)
    assert len(fake_sessions) == 1 and not fake_sessions[0].closed


def test_a_running_program_asks_first(window, three_shells, fake_sessions, monkeypatch):
    press_pane_button(window, 0)
    fake_sessions[0].idle = False
    questions = []
    monkeypatch.setattr(window, "_confirm", lambda text, _dangerous=False: questions.append(text) or False)
    choose(window, 0, "gitbash")
    assert len(questions) == 1 and "programa en marcha" in questions[0]
    assert len(fake_sessions) == 1 and not fake_sessions[0].closed
    assert window.terminal_shell_boxes[0].currentData() == CMD  # la lista vuelve a lo que se está ejecutando
    monkeypatch.setattr(window, "_confirm", lambda _text, _dangerous=False: True)
    choose(window, 0, "gitbash")
    assert len(fake_sessions) == 2 and wait_closed(fake_sessions[0])


def test_a_new_shell_that_does_not_start_leaves_the_old_one_running(window, three_shells, fake_sessions, monkeypatch):
    press_pane_button(window, 0)
    old = fake_sessions[0]
    messages = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QMessageBox.warning", lambda _parent, _title, text: messages.append(text))

    def fail(*_args):
        raise OSError("no arranca")

    monkeypatch.setattr(TerminalView, "session_factory", fail)
    choose(window, 0, "gitbash")
    assert messages == ["No se pudo abrir la terminal: no arranca"]
    terminal = window.terminals[0]
    assert terminal.session is old and not old.closed and terminal.running_shell == CMD_SHELL and not window.terminal_panels[0].isHidden()
    assert window.terminal_shell_boxes[0].currentData() == CMD and 0 not in window._terminal_choices


def test_what_is_left_of_the_old_shell_does_not_reach_the_new_one(window, three_shells, fake_sessions):
    press_pane_button(window, 0)
    old = fake_sessions[0]
    choose(window, 0, "gitbash")
    terminal, new = window.terminals[0], fake_sessions[1]
    new.on_data(b"nuevo")
    old.on_data(b"viejo")
    old.on_exit()  # termina el anterior: no debe cerrar la terminal
    assert wait_until(lambda: "nuevo" in "".join(terminal.screen_.display()))
    settle()
    assert "viejo" not in "".join(terminal.screen_.display())
    assert terminal.session is new and not window.terminal_panels[0].isHidden()


def test_what_arrives_after_the_terminal_is_disposed_touches_nothing(window, three_shells, fake_sessions):
    press_pane_button(window, 0)
    session, terminal = fake_sessions[0], window.terminals[0]
    received = []
    terminal._received.connect(lambda _generation, data: received.append(data))
    terminal._ended.connect(lambda _generation: received.append("fin"))
    terminal.dispose()
    session.on_data(b"tarde")
    session.on_exit()
    settle()
    assert received == [] and terminal._credits._value == 4


def test_the_wheel_never_changes_the_shell(window, three_shells, fake_sessions):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QWheelEvent
    press_pane_button(window, 0)
    box = window.terminal_shell_boxes[0]
    assert isinstance(box, ShellBox)
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton, NO_MODIFIER, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(box, event)
    settle()
    assert box.currentData() == CMD and len(fake_sessions) == 1


def test_a_terminal_with_its_own_choice_keeps_it_after_exit_and_the_others_use_preferences(window, three_shells, fake_sessions):
    press_pane_button(window, 0)
    choose(window, 0, "gitbash")
    fake_sessions[1].on_exit()
    settle()
    assert window.terminal_panels[0].isHidden()
    press_pane_button(window, 0)
    assert fake_sessions[2].argv == BASH_SHELL.argv
    window.config["terminal_shell"] = POWERSHELL
    press_pane_button(window, 1)
    assert fake_sessions[3].argv == PS_SHELL.argv


def test_custom_program_from_preferences_is_also_in_the_list(window, three_shells, fake_sessions):
    window.config["terminal_command"] = "/opt/mi-shell -l"
    press_pane_button(window, 0)
    box = window.terminal_shell_boxes[0]
    row = box.findData(CUSTOM)
    assert box.itemText(row) in ("mi-shell", "/opt/mi-shell")
    choose(window, 0, CUSTOM)
    assert fake_sessions[1].argv[-1] == "-l"


def test_preferences_changed_while_open_keep_showing_what_runs(window, three_shells, fake_sessions):
    press_pane_button(window, 0)
    window.config["terminal_shell"] = POWERSHELL
    window._preferences_changed()
    assert window.terminal_shell_boxes[0].currentData() == CMD and len(fake_sessions) == 1


def test_session_remembers_the_shell_of_each_terminal(qapp_offscreen, tmp_path, monkeypatch, folders, fake_sessions, three_shells):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    first = MainWindow(Config(), [str(folder) for folder in folders])
    first.resize(1500, 800)
    first.show()
    QApplication.processEvents()
    press_pane_button(first, 0)
    choose(first, 0, "gitbash")
    first.close()
    first.deleteLater()
    QApplication.processEvents()
    fake_sessions.clear()

    second = MainWindow(Config(), [str(folder) for folder in folders])
    second.resize(1500, 800)
    second.show()
    settle()
    assert fake_sessions[0].argv == BASH_SHELL.argv and second.terminal_shell_boxes[0].currentData() == "gitbash"
    second.close()
    second.deleteLater()
    QApplication.processEvents()


def test_remembered_shell_no_longer_installed_can_be_opened_again(window, monkeypatch, fake_sessions):
    monkeypatch.setattr(shells, "available_shells", lambda **_search: [CMD_SHELL])
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QMessageBox.warning", lambda _parent, _title, _text: None)
    window._terminal_choices[0] = "gitbash"
    press_pane_button(window, 0)
    assert window.terminal_panels[0].isHidden() and not fake_sessions
    press_pane_button(window, 0)
    assert not window.terminal_panels[0].isHidden() and fake_sessions[0].argv == CMD_SHELL.argv


def change_preference(window, key: str) -> None:
    window.config["terminal_shell"] = key
    window._preferences_changed()
    settle()


@pytest.mark.parametrize("command, index", [(GENERAL_TERMINAL_COMMAND, GENERAL_TERMINAL), (TERMINAL_TOGGLE_COMMAND, 0)])
def test_a_hidden_terminal_reopens_with_the_shell_of_the_preferences(window, three_shells, fake_sessions, tmp_path, command, index):
    window.run_command(command)
    settle()
    old = fake_sessions[0]
    old.directory = str(tmp_path)
    window.run_command(command)
    assert window.terminal_panels[index].isHidden()
    change_preference(window, "gitbash")
    assert len(fake_sessions) == 1 and not old.closed  # aún no: se cambia al volver a abrirla
    window.run_command(command)
    settle()
    assert len(fake_sessions) == 2 and fake_sessions[1].argv == BASH_SHELL.argv and same(fake_sessions[1].cwd, tmp_path)
    assert wait_closed(old)
    assert window.terminal_shell_boxes[index].currentData() == "gitbash" and window.focusWidget() is window.terminals[index]


def test_preferences_override_what_was_chosen_in_the_list(window, three_shells, fake_sessions):
    press_general(window)
    choose(window, GENERAL_TERMINAL, POWERSHELL)
    press_general(window)
    change_preference(window, "gitbash")
    assert window._terminal_choices == {}
    press_general(window)
    assert fake_sessions[-1].argv == BASH_SHELL.argv


def test_accepting_preferences_without_changing_the_terminal_keeps_the_list_choice(window, three_shells, fake_sessions):
    press_general(window)
    choose(window, GENERAL_TERMINAL, POWERSHELL)
    press_general(window)
    window._preferences_changed()
    press_general(window)
    assert window.terminals[GENERAL_TERMINAL].running_shell == PS_SHELL and len(fake_sessions) == 2


def test_list_choice_survives_hiding_and_showing(window, three_shells, fake_sessions):
    press_general(window)
    choose(window, GENERAL_TERMINAL, "gitbash")
    press_general(window)
    press_general(window)
    assert len(fake_sessions) == 2 and window.terminals[GENERAL_TERMINAL].running_shell == BASH_SHELL


def test_visible_terminal_keeps_its_shell_until_it_is_reopened(window, three_shells, fake_sessions):
    press_general(window)
    change_preference(window, "gitbash")
    window.run_command("Set GENERALTERMINAL=On")  # ya estaba a la vista: no se cierra
    settle()
    assert len(fake_sessions) == 1 and not fake_sessions[0].closed
    assert window.terminal_shell_boxes[GENERAL_TERMINAL].currentData() == CMD


def test_hidden_terminal_with_a_running_program_is_not_closed(window, three_shells, fake_sessions):
    press_general(window)
    press_general(window)
    change_preference(window, "gitbash")
    fake_sessions[0].idle = False
    messages = []
    window.statusBar().messageChanged.connect(messages.append)
    press_general(window)
    assert len(fake_sessions) == 1 and not fake_sessions[0].closed and not window.terminal_panels[GENERAL_TERMINAL].isHidden()
    assert any("programa en marcha" in message for message in messages)
    assert window.terminal_shell_boxes[GENERAL_TERMINAL].currentData() == CMD
    press_general(window)
    fake_sessions[0].idle = True
    press_general(window)  # ya terminó: esta vez sí cambia
    assert len(fake_sessions) == 2 and fake_sessions[1].argv == BASH_SHELL.argv


def test_hidden_terminal_whose_new_shell_does_not_start_stays_as_it_was(window, three_shells, fake_sessions, monkeypatch):
    press_general(window)
    press_general(window)
    change_preference(window, "gitbash")
    messages = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QMessageBox.warning", lambda _parent, _title, text: messages.append(text))

    def fail(*_args):
        raise OSError("no arranca")

    monkeypatch.setattr(TerminalView, "session_factory", fail)
    press_general(window)
    assert messages == ["No se pudo abrir la terminal: no arranca"]
    assert window.terminals[GENERAL_TERMINAL].session is fake_sessions[0] and not fake_sessions[0].closed


def test_cmd_saved_by_the_previous_version_is_no_longer_forced(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 9, "terminal_shell": "cmd"}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert config["terminal_shell"] == ""
    config["terminal_shell"] = "cmd"  # elegida a propósito a partir de ahora
    config.save()
    assert Config(str(tmp_path))["terminal_shell"] == "cmd"


def test_other_shell_saved_by_the_previous_version_stays(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 9, "terminal_shell": "gitbash"}), encoding="utf-8")
    assert Config(str(tmp_path))["terminal_shell"] == "gitbash"


# ------------------------------------------------------------------ la terminal general a todo el ancho
def set_full_width(window, on: bool = True) -> None:
    window.config["general_terminal_full_width"] = on
    window._preferences_changed()
    settle()


def left_of(widget, window) -> int:
    return widget.mapTo(window, QPoint(0, 0)).x()


def right_of(widget, window) -> int:
    return widget.mapTo(window, QPoint(widget.width(), 0)).x()


def top_of(widget, window) -> int:
    return widget.mapTo(window, QPoint(0, 0)).y()


def bottom_of(widget, window) -> int:
    return widget.mapTo(window, QPoint(0, widget.height())).y()


def test_full_width_goes_below_the_tree_and_the_viewer(window):
    window.apply_style("Vertical doble panel")
    window.run_command("Set TREE=On")
    window.run_command("Set VIEWPANE=On")
    settle()
    assert not window.tree_dock.isHidden() and not window.viewer_dock.isHidden()
    set_full_width(window)
    press_general(window)
    panel = window.terminal_panels[GENERAL_TERMINAL]
    assert window.general_dock.isVisible() and panel.isVisible()
    assert left_of(panel, window) <= left_of(window.tree_dock, window)
    assert right_of(panel, window) >= right_of(window.viewer_dock, window)
    assert top_of(panel, window) >= bottom_of(window.tree_dock, window)
    assert top_of(panel, window) >= bottom_of(window.viewer_dock, window)
    assert top_of(panel, window) >= bottom_of(window.groups[0], window)
    assert panel.height() >= 160 and window.groups[0].height() > panel.height()


def test_without_the_option_it_stays_below_the_panes_only(window):
    window.apply_style("Vertical doble panel")
    window.run_command("Set TREE=On")
    settle()
    press_general(window)
    panel = window.terminal_panels[GENERAL_TERMINAL]
    assert not window.general_dock.isVisible()
    assert left_of(panel, window) >= right_of(window.tree_dock, window)
    assert panel.parent() is window.work_area


def test_turning_it_on_and_off_with_the_terminal_open_keeps_the_session(window, fake_sessions):
    window.apply_style("Vertical doble panel")
    press_general(window)
    terminal, panel = window.terminals[GENERAL_TERMINAL], window.terminal_panels[GENERAL_TERMINAL]
    assert window.focusWidget() is terminal
    set_full_width(window)
    assert window.general_dock.isVisible() and panel.isVisible() and window.focusWidget() is terminal
    assert panel.width() >= window.centralWidget().width()
    set_full_width(window, False)
    assert not window.general_dock.isVisible() and panel.isVisible() and panel.parent() is window.work_area and window.focusWidget() is terminal
    assert window.work_area.widget(1) is panel and panel.height() >= 160
    assert len(fake_sessions) == 1 and not fake_sessions[0].closed


def test_hiding_and_showing_with_full_width(window, fake_sessions):
    set_full_width(window)
    press_general(window)
    press_general(window)
    assert window.general_dock.isHidden() and window.terminal_panels[GENERAL_TERMINAL].isHidden()
    press_general(window)
    assert window.general_dock.isVisible() and len(fake_sessions) == 1
    next(button for button in window.terminal_panels[GENERAL_TERMINAL].findChildren(type(window.groups[0].add_button)) if button.toolTip() == "Ocultar").click()
    assert window.general_dock.isHidden()
    window.terminal_panels[GENERAL_TERMINAL].show()
    press_general(window)
    fake_sessions[0].on_exit()
    settle()
    assert window.general_dock.isHidden()


def test_horizontal_mode_always_goes_right_of_the_panes(window):
    set_full_width(window)
    press_general(window)
    assert window.general_dock.isVisible()
    window.apply_style("Horizontal doble panel")
    settle()
    panel = window.terminal_panels[GENERAL_TERMINAL]
    assert not window.general_dock.isVisible() and panel.isVisible() and panel.parent() is window.work_area
    assert left_of(panel, window) >= right_of(window.groups[0], window)
    window.apply_style("Vertical doble panel")
    settle()
    assert window.general_dock.isVisible() and panel.isVisible()


def test_full_width_with_a_single_pane(window):
    set_full_width(window)
    window.apply_style("Explorador")
    settle()
    press_general(window)
    assert window.general_dock.isVisible()
    assert left_of(window.terminal_panels[GENERAL_TERMINAL], window) <= left_of(window.tree_dock, window)


def test_full_width_shares_the_bottom_with_the_utilities(window):
    set_full_width(window)
    press_general(window)
    window.run_command("Set UTILITY=Log")
    settle()
    panel = window.terminal_panels[GENERAL_TERMINAL]
    assert window.utility_dock.isVisible() and panel.isVisible()
    assert panel.width() >= window.centralWidget().width()


def test_popup_menu_of_the_bars_does_not_offer_the_empty_dock(window):
    menu = window.createPopupMenu()
    assert not any(action.isVisible() and action.text() == "Terminal general" for action in menu.actions())


def test_preference_for_full_width_and_session(qapp_offscreen, tmp_path, monkeypatch, folders, fake_sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    first = MainWindow(Config(), [str(folder) for folder in folders])
    first.resize(1500, 800)
    first.show()
    QApplication.processEvents()
    dialog = PreferencesDialog(first.config)
    assert not dialog.general_terminal_full_width.isChecked()
    dialog.general_terminal_full_width.setChecked(True)
    dialog.apply_to(first.config)
    dialog.deleteLater()
    first._preferences_changed()
    press_general(first)
    first.resizeDocks([first.general_dock], [300], Qt.Orientation.Vertical)
    settle()
    height = first.terminal_panels[GENERAL_TERMINAL].height()
    first.close()
    first.deleteLater()
    QApplication.processEvents()

    second = MainWindow(Config(), [str(folder) for folder in folders])
    second.resize(1500, 800)
    second.show()
    settle()
    assert second.general_dock.isVisible() and second.terminal_panels[GENERAL_TERMINAL].isVisible()
    assert wait_until(lambda: abs(second.terminal_panels[GENERAL_TERMINAL].height() - height) < 40, 3)
    assert len(fake_sessions) == 2
    second.close()
    second.deleteLater()
    QApplication.processEvents()


def test_saved_session_without_the_dock_in_its_layout_still_opens(qapp_offscreen, tmp_path, monkeypatch, folders, fake_sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    config = Config()
    config["general_terminal_full_width"] = True
    config["session"] = {"general_terminal": {"sizes": "", "visible": True, "follow": True}}
    second = MainWindow(config, [str(folder) for folder in folders])
    second.resize(1500, 800)
    second.show()
    settle()
    panel = second.terminal_panels[GENERAL_TERMINAL]
    assert second.general_dock.isVisible() and panel.isVisible() and panel.height() >= 160
    second.close()
    second.deleteLater()
    QApplication.processEvents()


# ------------------------------------------------------------------ el intérprete elegido
def test_the_chosen_shell_is_the_one_started_with_its_environment(window, fake_sessions, monkeypatch):
    chosen = Shell("gitbash", "Git Bash", ["C:\\usr\\bin\\bash.exe", "--login", "-i"], POSIX, {"MSYSTEM": "MINGW64"})
    monkeypatch.setattr(shells, "available_shells", lambda **_search: [Shell(CMD, "cmd", ["cmd.exe"], CMD), chosen])
    window.config["terminal_shell"] = "gitbash"
    press_pane_button(window, 0)
    assert fake_sessions[0].argv == chosen.argv and fake_sessions[0].env == chosen.env


def test_shell_family_decides_the_command_and_the_prompt(window, fake_sessions, folders, monkeypatch):
    chosen = Shell(POWERSHELL, "PowerShell", ["powershell.exe"], POWERSHELL)
    monkeypatch.setattr(shells, "available_shells", lambda **_search: [chosen])
    window.config["terminal_shell"] = POWERSHELL
    press_pane_button(window, 0)
    terminal, session = window.terminals[0], fake_sessions[0]
    terminal.screen_.feed_text("PS C:\\> ")
    terminal._last_activity = 0
    window.groups[0].current.navigate(str(folders[1]))
    assert wait_until(lambda: session.sent)
    assert session.sent == cd_command(str(folders[1]), POWERSHELL).encode()


def test_shell_no_longer_installed_is_reported(window, monkeypatch):
    monkeypatch.setattr(shells, "available_shells", lambda **_search: [Shell(CMD, "cmd", ["cmd.exe"], CMD)])
    window.config["terminal_shell"] = "gitbash"
    messages = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QMessageBox.warning", lambda _parent, _title, text: messages.append(text))
    press_general(window)
    assert window.terminal_panels[GENERAL_TERMINAL].isHidden()
    assert "gitbash" in messages[0]


def test_preferences_offer_the_installed_shells_and_another_program(window, monkeypatch):
    monkeypatch.setattr(shells, "available_shells", lambda **_search: [Shell(CMD, "cmd", ["cmd.exe"], CMD), Shell("gitbash", "Git Bash", ["bash.exe"], POSIX)])
    window.config["terminal_shell"] = "gitbash"
    dialog = PreferencesDialog(window.config)
    box = dialog.terminal_shell
    assert [box.itemData(row) for row in range(box.count())] == [CMD, "gitbash", CUSTOM]
    assert box.currentData() == "gitbash" and dialog.terminal_command.isHidden()
    box.setCurrentIndex(box.findData(CUSTOM))
    assert not dialog.terminal_command.isHidden() and not dialog.browse_terminal.isHidden()
    dialog.terminal_command.setText(" zsh -l ")
    dialog.apply_to(window.config)
    assert window.config["terminal_shell"] == CUSTOM and window.config["terminal_command"] == "zsh -l"
    dialog.terminal_command.setText("")
    dialog.apply_to(window.config)
    assert window.config["terminal_shell"] == ""  # sin programa se vuelve al de siempre
    box.setCurrentIndex(1)
    dialog.apply_to(window.config)
    assert window.config["terminal_shell"] == "gitbash"
    box.setCurrentIndex(0)
    dialog.apply_to(window.config)
    assert window.config["terminal_shell"] == ""  # la predeterminada no se guarda: así sigue siéndolo aunque cambie
    dialog.deleteLater()


def test_a_settings_file_cannot_choose_the_terminal_program(tmp_path):
    path = tmp_path / "ajustes.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "settings": {"terminal_shell": CUSTOM, "terminal_command": "C:\\malicioso.exe"}}), encoding="utf-8")
    settings, ignored = settings_io.read_settings(str(path))
    assert "terminal_command" not in settings and {"terminal_shell", "terminal_command"} <= set(ignored)  # se ejecutaría al abrir una terminal
    assert "terminal_command" not in settings_io.portable_settings({"terminal_shell": CUSTOM, "terminal_command": "x"})


def test_external_terminal_uses_the_chosen_shell_on_windows(window, monkeypatch):
    calls = []
    monkeypatch.setattr("directory_commander_tux_tux.core.platform_utils.open_terminal", lambda path, shell=None: calls.append(shell))
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.sys.platform", "win32")
    default = Shell(POWERSHELL, "PowerShell", ["powershell.exe"], POWERSHELL)
    chosen = Shell("gitbash", "Git Bash", ["bash.exe"], POSIX)
    monkeypatch.setattr(shells, "available_shells", lambda **_search: [default, chosen])
    window.run_command(TERMINAL_COMMAND)
    window.config["terminal_shell"] = "gitbash"
    window.run_command(TERMINAL_COMMAND)
    assert calls == [default, chosen]


def test_view_menu_has_the_shortcut(window):
    actions = [action for menu in window.main_menu.findChildren(type(window.favorites_menu)) for action in menu.actions()]
    action = next(action for action in actions if action.text() == "Terminal del panel")
    assert action.shortcut().toString() == TOGGLE_SHORTCUT


def test_hiding_keeps_the_session_and_showing_does_not_start_another(window, fake_sessions):
    press_pane_button(window, 0)
    session = fake_sessions[0]
    press_pane_button(window, 0)
    assert window.terminal_panels[0].isHidden() and not session.closed
    press_pane_button(window, 0)
    assert fake_sessions == [session]


def test_close_button_of_the_title_bar_hides_it(window):
    press_pane_button(window, 0)
    panel = window.terminal_panels[0]
    next(button for button in panel.findChildren(type(window.groups[0].add_button)) if button.toolTip() == "Ocultar").click()
    assert panel.isHidden()


def test_focus_goes_to_the_terminal_and_back_to_the_list(window):
    press_pane_button(window, 0)
    terminal = window.terminals[0]
    assert window.focusWidget() is terminal
    window.run_command(TERMINAL_TOGGLE_COMMAND)
    assert window.focusWidget() is not terminal


def test_exit_hides_it_and_the_next_one_starts_in_the_current_folder(window, folders, fake_sessions):
    press_pane_button(window, 0)
    fake_sessions[0].on_exit()
    settle()
    assert window.terminal_panels[0].isHidden() and window.terminals[0].session is None
    window.groups[0].current.navigate(str(folders[1]))
    settle()
    press_pane_button(window, 0)
    assert len(fake_sessions) == 2 and same(fake_sessions[1].cwd, folders[1])


def test_clicking_the_terminal_activates_its_pane(window):
    press_pane_button(window, 1)
    press_pane_button(window, 0)
    assert window.active_side == 0
    window.terminals[1].focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
    assert window.active_side == 1


def test_start_failure_is_reported_and_the_panel_stays_closed(window, monkeypatch):
    def fail(*_args):
        raise OSError("sin consola")

    monkeypatch.setattr(TerminalView, "session_factory", fail)
    messages = []
    monkeypatch.setattr("directory_commander_tux_tux.ui.main_window.QMessageBox.warning", lambda _parent, _title, text: messages.append(text))
    press_pane_button(window, 0)
    assert window.terminal_panels[0].isHidden()
    assert messages == ["No se pudo abrir la terminal: sin consola"]


def test_folder_inside_an_archive_or_computer_uses_home(window, monkeypatch):
    monkeypatch.setattr(window.groups[0].current, "path", "C:\\no\\existe.zip\\dentro")
    from directory_commander_tux_tux.core import platform_utils
    assert window._terminal_folder(0) == platform_utils.home_dir()


def test_real_shell_in_the_window_from_button_to_exit(window, folders, monkeypatch):
    monkeypatch.setattr(TerminalView, "session_factory", PtySession)
    press_pane_button(window, 0)
    terminal = window.terminals[0]
    assert wait_until(lambda: terminal.session is not None and any(terminal.screen_.display()), 60)  # el primer intérprete de un equipo recién arrancado tarda
    terminal._send_text("echo escrito_en_la_terminal\r")
    assert wait_until(lambda: "".join(terminal.screen_.display()).count("escrito_en_la_terminal") >= 2)
    terminal.grab()
    terminal._send_text("exit\r")
    assert wait_until(lambda: window.terminal_panels[0].isHidden())
    assert terminal.session is None


REAL_SHELLS = [shell for shell in available_shells() if shell.family != OTHER]


@pytest.mark.parametrize("shell", REAL_SHELLS, ids=[shell.key for shell in REAL_SHELLS])
def test_every_installed_shell_starts_in_the_folder_and_follows_the_pane(window, folders, monkeypatch, shell):
    """Con la terminal general, que usa lo mismo que la de cada panel."""
    monkeypatch.setattr(TerminalView, "session_factory", PtySession)
    window.config["terminal_shell"] = shell.key
    press_general(window)
    terminal = window.terminals[GENERAL_TERMINAL]
    assert wait_until(lambda: terminal.session is not None and looks_like_prompt(terminal.screen_.prompt_text(), shell.family), 45), terminal.screen_.display()
    terminal._send_text(("cd" if shell.family == CMD else "pwd") + "\r")
    assert wait_until(lambda: "izquierda" in screen_text(terminal), 20), terminal.screen_.display()
    assert wait_until(lambda: looks_like_prompt(terminal.screen_.prompt_text(), shell.family) and terminal.session.shell_is_idle(), 20), terminal.screen_.display()
    window.groups[0].current.navigate(str(folders[1]))
    assert wait_until(lambda: terminal._wanted_folder is None and terminal._current_folder == str(folders[1]), 20), terminal.screen_.display()
    assert wait_until(lambda: looks_like_prompt(terminal.screen_.prompt_text(), shell.family) and terminal.session.shell_is_idle(), 20), terminal.screen_.display()
    terminal._send_text(("cd" if shell.family == CMD else "pwd") + "\r")
    assert wait_until(lambda: screen_text(terminal).count("derecha") >= 2, 20), terminal.screen_.display()
    terminal._send_text("exit\r")
    assert wait_until(lambda: window.terminal_panels[GENERAL_TERMINAL].isHidden(), 20), terminal.screen_.display()


def terminal_state(terminal) -> dict:
    """Lo que hace falta para entender, desde el registro, por qué no se la da por lista."""
    screen = terminal.screen_
    return {"pantalla": screen.display(), "cursor": (screen.x, screen.y), "tamaño": (screen.cols, screen.rows), "antes del cursor": screen.before_cursor(), "indicador": screen.prompt_text(),
            "libre": terminal.session.shell_is_idle() if terminal.session is not None else None}


def real_prompt_ready(terminal, family, seconds=45) -> bool:
    return wait_until(lambda: terminal.session is not None and looks_like_prompt(terminal.screen_.prompt_text(), family) and terminal.session.shell_is_idle(), seconds)


@pytest.mark.parametrize("shell", REAL_SHELLS, ids=[shell.key for shell in REAL_SHELLS])
def test_changing_shell_keeps_the_folder_reached_with_cd(window, folders, monkeypatch, shell):
    """De cada intérprete instalado a otro, en una carpeta a la que se ha llegado escribiendo cd y no con el panel."""
    inside = folders[0] / "con cd ñ"
    inside.mkdir()
    monkeypatch.setattr(TerminalView, "session_factory", PtySession)
    window.config["terminal_shell"] = shell.key
    press_pane_button(window, 0)
    terminal = window.terminals[0]
    assert real_prompt_ready(terminal, shell.family), terminal_state(terminal)
    terminal._send_text('cd "con cd ñ"\r')
    assert wait_until(lambda: same(terminal.current_path(), inside), 20), (terminal.screen_.display(), terminal.current_path())
    target = next(other for other in REAL_SHELLS if other.key != shell.key)
    assert real_prompt_ready(terminal, shell.family), terminal.screen_.display()
    choose(window, 0, target.key)
    assert terminal.running_shell.key == target.key
    assert real_prompt_ready(terminal, target.family), terminal.screen_.display()
    assert same(terminal._current_folder, inside)
    terminal._send_text(("cd" if target.family == CMD else "pwd") + "\r")
    # Sin los espacios: la ruta puede partirse en dos filas justo en uno de ellos.
    assert wait_until(lambda: "concdñ" in screen_text(terminal).replace(" ", ""), 20), terminal.screen_.display()
    assert real_prompt_ready(terminal, target.family), terminal.screen_.display()
    window.groups[0].current.navigate(str(folders[1]))
    assert wait_until(lambda: terminal._wanted_folder is None and terminal._current_folder == str(folders[1]), 20), terminal.screen_.display()
    terminal._send_text("exit\r")
    assert wait_until(lambda: window.terminal_panels[0].isHidden(), 20), terminal.screen_.display()


def real_shell_ready(window, monkeypatch, side=0):
    monkeypatch.setattr(TerminalView, "session_factory", PtySession)
    press_pane_button(window, side)
    terminal = window.terminals[side]
    assert wait_until(lambda: terminal.session is not None and terminal.screen_.before_cursor().endswith((">", "$", "#", "%")))
    return terminal


def screen_text(terminal) -> str:
    return "".join(terminal.screen_.display())  # sin saltos: una ruta larga ocupa varias filas


def test_real_shell_follows_the_pane(window, folders, monkeypatch):
    terminal = real_shell_ready(window, monkeypatch)
    window.groups[0].current.navigate(str(folders[1]))
    assert wait_until(lambda: terminal._current_folder == str(folders[1]) and terminal.session.shell_is_idle())
    # PowerShell da «pwd» en una tabla, que recorta con «...» una ruta más ancha que la terminal: su texto, entero.
    terminal._send_text("(pwd).Path\r" if terminal.family == POWERSHELL else "pwd\r")
    assert wait_until(lambda: screen_text(terminal).count("derecha") >= 3)


def test_real_shell_waits_for_a_running_program_and_then_follows(window, folders, monkeypatch):
    terminal = real_shell_ready(window, monkeypatch)
    terminal._send_text("ping -n 4 127.0.0.1\r" if sys.platform == "win32" else "sleep 3\r")
    assert wait_until(lambda: not terminal.session.shell_is_idle())
    window.groups[0].current.navigate(str(folders[1]))
    QTest.qWait(600)
    after = screen_text(terminal).split("127.0.0.1")[-1]
    assert terminal._wanted_folder == str(folders[1]) and "cd " not in after and "Set-Location" not in after
    assert wait_until(lambda: terminal._wanted_folder is None, 20)
    assert terminal._current_folder == str(folders[1])


def ready_fake(window):
    """Una terminal con su indicador de comandos, y ya pasado el momento justo después de escribir."""
    terminal, session = open_terminal(window)
    terminal.screen_.feed_text("C:\\>")
    terminal._last_activity = 0
    return terminal, session


def test_following_writes_cd_at_an_empty_prompt(window, folders):
    terminal, session = ready_fake(window)
    window.groups[0].current.navigate(str(folders[1]))
    assert wait_until(lambda: session.sent)
    assert session.sent == cd_command(str(folders[1])).encode()
    assert terminal._current_folder == str(folders[1])


def test_same_folder_writes_nothing(window, folders):
    terminal, session = ready_fake(window)
    terminal.follow(terminal._current_folder)
    window.groups[0].current.refresh()
    QTest.qWait(50)
    assert session.sent == b""


def test_each_terminal_follows_only_its_pane(window, folders, tmp_path):
    other = tmp_path / "otra"
    other.mkdir()
    terminal, session = ready_fake(window)
    window.groups[1].current.navigate(str(other))
    QTest.qWait(50)
    assert session.sent == b""


def test_half_typed_text_is_never_overwritten_and_enter_lets_it_follow(window, folders):
    terminal, session = ready_fake(window)
    QTest.keyClick(terminal, Qt.Key.Key_R)
    session.sent = b""
    terminal._last_activity = 0
    window.groups[0].current.navigate(str(folders[1]))
    QTest.qWait(320)
    assert session.sent == b"" and terminal._wanted_folder == str(folders[1])
    QTest.keyClick(terminal, Qt.Key.Key_Return)
    assert session.sent == b"\r"
    session.sent = b""
    terminal._last_activity = 0
    assert wait_until(lambda: session.sent)
    assert session.sent == cd_command(str(folders[1])).encode()


def test_ctrl_c_also_leaves_the_line_empty(window, folders):
    terminal, session = ready_fake(window)
    QTest.keyClick(terminal, Qt.Key.Key_R)
    QTest.keyClick(terminal, Qt.Key.Key_C, CTRL)
    assert not terminal._line_started


@pytest.mark.parametrize("change", ["busy", "no_prompt", "full_screen", "just_typed"])
def test_it_waits_when_the_shell_cannot_receive_a_command(window, folders, change):
    terminal, session = ready_fake(window)
    if change == "busy":
        session.idle = False
    elif change == "no_prompt":
        terminal.screen_.feed_text("\r\n")
    elif change == "full_screen":
        terminal.screen_.feed_text("\x1b[?1049h\x1b[Hvim>")
    terminal._last_activity = time.monotonic() + 60 if change == "just_typed" else 0
    window.groups[0].current.navigate(str(folders[1]))
    QTest.qWait(320)
    assert session.sent == b"" and terminal._wanted_folder == str(folders[1])


@pytest.mark.skipif(sys.platform != "win32", reason="el indicador de cmd")
def test_a_batch_file_question_is_not_a_prompt(window, folders):
    terminal, session = ready_fake(window)
    terminal.screen_.feed_text("\r\nPresione una tecla para continuar . . . ")
    terminal._last_activity = 0
    window.groups[0].current.navigate(str(folders[1]))
    QTest.qWait(320)
    assert session.sent == b""


def test_latest_folder_wins_while_waiting(window, folders, tmp_path):
    other = tmp_path / "otra"
    other.mkdir()
    terminal, session = ready_fake(window)
    session.idle = False
    window.groups[0].current.navigate(str(folders[1]))
    window.groups[0].current.navigate(str(other))
    QTest.qWait(50)
    session.idle = True
    assert wait_until(lambda: session.sent)
    assert session.sent == cd_command(str(other)).encode()


def test_changing_tab_follows_too(window, folders):
    terminal, session = ready_fake(window)
    window.groups[0].add_tab(str(folders[1]))
    assert wait_until(lambda: session.sent)
    assert session.sent == cd_command(str(folders[1])).encode()


def test_hidden_terminal_follows_as_well(window, folders):
    terminal, session = ready_fake(window)
    window.run_command(TERMINAL_TOGGLE_COMMAND)
    assert window.terminal_panels[0].isHidden()
    window.groups[0].current.navigate(str(folders[1]))
    assert wait_until(lambda: session.sent)


def test_not_started_terminal_starts_where_the_pane_is(window, folders, fake_sessions):
    window.groups[0].current.navigate(str(folders[1]))
    QTest.qWait(30)
    assert not fake_sessions
    press_pane_button(window, 0)
    assert same(fake_sessions[0].cwd, folders[1])


def test_following_does_not_move_the_view_or_drop_the_selection(window, folders):
    terminal, session = ready_fake(window)
    terminal.screen_.feed_text("\r\n".join(str(number) for number in range(200)) + "\r\nC:\\>")
    terminal._refresh()
    terminal._last_activity = 0
    bar = terminal.verticalScrollBar()
    bar.setValue(3)
    terminal._selection = ((0, 0), (0, 1))
    window.groups[0].current.navigate(str(folders[1]))
    assert wait_until(lambda: session.sent)
    assert bar.value() == 3 and terminal._selection == ((0, 0), (0, 1))


# ------------------------------------------------------------------ teclado y portapapeles en el widget
def open_terminal(window, side: int = 0):
    press_pane_button(window, side)
    window.activateWindow()
    terminal = window.terminals[side]
    terminal.setFocus()
    QApplication.processEvents()
    return terminal, FakeSession.instances[-1]


def test_typing_reaches_the_shell_and_window_shortcuts_do_not_steal_keys(window, monkeypatch):
    terminal, session = open_terminal(window)
    ran = []
    monkeypatch.setattr(window, "run_command", ran.append)
    QTest.keyClicks(terminal, "dir")
    QTest.keyClick(terminal, Qt.Key.Key_Return)
    assert session.sent == b"dir\r"
    session.sent = b""
    for key, modifiers, expected in ((Qt.Key.Key_A, CTRL, b"\x01"), (Qt.Key.Key_Delete, NO_MODIFIER, b"\x1b[3~"), (Qt.Key.Key_F5, NO_MODIFIER, b"\x1b[15~"), (Qt.Key.Key_Tab, NO_MODIFIER, b"\t")):
        QTest.keyClick(terminal, key, modifiers)
        assert session.sent == expected
        session.sent = b""
    assert ran == []


def test_shortcut_override_only_lets_window_keys_through(window):
    from PySide6.QtGui import QKeyEvent
    terminal, _session = open_terminal(window)

    def accepted(key, modifiers):
        event = QKeyEvent(QEvent.Type.ShortcutOverride, key, modifiers)
        event.ignore()
        terminal.event(event)
        return event.isAccepted()

    assert accepted(Qt.Key.Key_Delete, NO_MODIFIER)
    assert accepted(Qt.Key.Key_C, CTRL)
    assert accepted(Qt.Key.Key_F3, NO_MODIFIER)
    assert not accepted(Qt.Key.Key_O, CTRL | SHIFT)
    assert not accepted(Qt.Key.Key_Tab, CTRL)
    assert not accepted(Qt.Key.Key_F4, ALT)


def test_selection_copy_and_paste(window):
    terminal, session = open_terminal(window)
    terminal.screen_.feed_text("hola mundo")
    terminal._refresh()
    width, height = terminal._cell_width, terminal._cell_height
    row = height // 2
    QTest.mousePress(terminal.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(0, row))
    terminal.mouseMoveEvent(_MoveEvent(QPoint(int(width * 4), row)))
    QTest.mouseRelease(terminal.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(int(width * 4), row))
    assert terminal.selected_text() == "hola"
    QTest.keyClick(terminal, Qt.Key.Key_C, CTRL)
    assert QApplication.clipboard().text() == "hola"
    assert session.sent == b""  # con algo seleccionado, Ctrl+C copia en vez de interrumpir
    assert terminal.selected_text() == ""
    QTest.keyClick(terminal, Qt.Key.Key_C, CTRL)
    assert session.sent == b"\x03"
    session.sent = b""
    QApplication.clipboard().setText("a\r\nb")
    terminal.paste()
    assert session.sent == b"a\rb"
    session.sent = b""
    terminal.screen_.feed_text("\x1b[?2004h")
    terminal.paste()
    assert session.sent == b"\x1b[200~a\rb\x1b[201~"


class _MoveEvent:
    def __init__(self, point):
        self._point = point

    def position(self):
        return self._point


def test_double_click_selects_the_word(window):
    terminal, _session = open_terminal(window)
    terminal.screen_.feed_text("uno C:\\ruta\\a.txt tres")
    x = int(terminal._cell_width * 8)
    QTest.mouseDClick(terminal.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x, terminal._cell_height // 2))
    assert terminal.selected_text() == "C:\\ruta\\a.txt"


def test_menu_copies_only_with_a_selection(window):
    terminal, _session = open_terminal(window)
    actions = terminal.build_menu().actions()
    assert [action.text() for action in actions] == ["Copiar", "Pegar"]
    assert not actions[0].isEnabled() and actions[1].isEnabled()


def test_output_scrolls_and_the_view_stays_where_the_user_left_it(window):
    terminal, _session = open_terminal(window)
    bar = terminal.verticalScrollBar()
    terminal.screen_.feed_text("\r\n".join(str(number) for number in range(200)))
    terminal._refresh()
    assert bar.maximum() > 0 and bar.value() == bar.maximum()
    bar.setValue(0)
    terminal.screen_.feed_text("\r\nmás")
    terminal._refresh()
    assert bar.value() == 0
    QTest.keyClick(terminal, Qt.Key.Key_X)
    assert bar.value() == bar.maximum()


def test_resize_reaches_the_screen_and_the_session(window):
    terminal, session = open_terminal(window)
    before = terminal.screen_.cols
    window.pane_areas[0].setSizes([200, 400])
    window.resize(1100, 700)
    settle(120)
    assert terminal.screen_.cols != before
    assert session.size == (terminal.screen_.cols, terminal.screen_.rows)


def test_painting_every_kind_of_cell_does_not_fail(window):
    terminal, _session = open_terminal(window)
    terminal.screen_.feed_text("\x1b[1;31mrojo\x1b[0m \x1b[7minverso\x1b[0m \x1b[2mtenue\x1b[0m \x1b[4;9mraya\x1b[0m 日本 é \x1b[38;2;1;2;3;48;5;240mrgb\x1b[0m")
    terminal._selection = ((terminal.screen_.dropped, 2), (terminal.screen_.dropped, 9))
    terminal.grab()
    terminal.clearFocus()
    terminal.grab()
    window.run_command("Set DARKMODE=Toggle")
    terminal.grab()


def test_terminal_icons_exist():
    assert not themed("terminalpane").isNull() and not themed("terminalgeneral").isNull()


# ------------------------------------------------------------------ sesión y configuración
def test_session_restores_open_terminals(qapp_offscreen, tmp_path, monkeypatch, folders, fake_sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    first = MainWindow(Config(), [str(folder) for folder in folders])
    first.resize(1500, 800)
    first.show()
    QApplication.processEvents()
    press_pane_button(first, 1)
    first.pane_areas[1].setSizes([300, 400])
    first.terminal_follow_buttons[1].click()
    settle()
    height = first.terminal_panels[1].height()
    first.close()
    assert fake_sessions[0].closed
    first.deleteLater()
    QApplication.processEvents()

    second = MainWindow(Config(), [str(folder) for folder in folders])
    second.resize(1500, 800)
    second.show()
    settle()
    assert not second.terminal_panels[1].isHidden() and second.terminal_panels[0].isHidden()
    assert len(fake_sessions) == 2 and same(fake_sessions[1].cwd, folders[1])
    assert abs(second.terminal_panels[1].height() - height) < 40
    assert second.terminals[0].following and not second.terminals[1].following and not second.terminal_follow_buttons[1].isChecked()
    second.close()
    second.deleteLater()
    QApplication.processEvents()


def test_session_restores_the_general_terminal(qapp_offscreen, tmp_path, monkeypatch, folders, fake_sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    first = MainWindow(Config(), [str(folder) for folder in folders])
    first.resize(1500, 800)
    first.show()
    QApplication.processEvents()
    press_general(first)
    first.work_area.setSizes([300, 400])
    first.terminal_follow_buttons[GENERAL_TERMINAL].click()
    settle()
    height = first.terminal_panels[GENERAL_TERMINAL].height()
    first.close()
    first.deleteLater()
    QApplication.processEvents()

    second = MainWindow(Config(), [str(folder) for folder in folders])
    second.resize(1500, 800)
    second.show()
    settle()
    panel = second.terminal_panels[GENERAL_TERMINAL]
    assert not panel.isHidden() and second.terminal_panels[0].isHidden() and second.terminal_panels[1].isHidden()
    assert abs(panel.height() - height) < 40 and not second.terminals[GENERAL_TERMINAL].following
    second.close()
    second.deleteLater()
    QApplication.processEvents()


def test_session_of_the_previous_version_still_opens(qapp_offscreen, tmp_path, monkeypatch, folders, fake_sessions):
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "cfg"))
    config = Config()
    config["session"] = {"pane_terminals": [{"sizes": "", "visible": True}, {"sizes": "", "visible": False}]}
    second = MainWindow(config, [str(folder) for folder in folders])
    second.resize(1500, 800)
    second.show()
    settle()
    assert not second.terminal_panels[0].isHidden() and second.terminal_panels[GENERAL_TERMINAL].isHidden()
    assert all(terminal.following for terminal in second.terminals)
    second.close()
    second.deleteLater()
    QApplication.processEvents()


def test_old_config_gets_the_button_next_to_open_terminal(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 7, "toolbar": [
        {"label": "Otro", "commands": ["Go UP"]}, {"label": "Terminal", "commands": [TERMINAL_COMMAND]}, {"label": "Último", "commands": ["Go BACK"]}],
        "bar_order": {"toolbar": ["Último", "Terminal", "Otro"], "pane": ["goup", "menu-arrow", "path"]}}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert [item["commands"][0] for item in config["toolbar"]] == ["Go UP", TERMINAL_COMMAND, GENERAL_TERMINAL_COMMAND, "Set PANEFRAMES=Toggle", "Go BACK", "Compare FILES", "Set GENERATIONMARKS=Toggle"]
    arranged = bar_buttons.arrange_toolbar(config, config["toolbar"])
    assert [item["label"] for item in arranged] == ["Último", "Comparar archivos", "Marcas de IA", "Terminal", "Terminal general", "Marcos de los paneles", "Otro"]
    order = bar_buttons.arranged(config, bar_buttons.PANE)
    assert order.index("terminalpane") == order.index("treepane") + 1 == order.index("gitbranch") + 2 and order.index("gitbranch") == order.index("copypath") + 1 and order.index("copypath") == order.index("menu-arrow") + 1


def test_toolbar_without_open_terminal_gets_it_at_the_end_and_only_once(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 7, "toolbar": [{"label": "Otro", "commands": ["Go UP"]}]}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert [item["commands"] for item in config["toolbar"][-4:]] == [[GENERAL_TERMINAL_COMMAND], ["Set PANEFRAMES=Toggle"], ["Compare FILES"], ["Set GENERATIONMARKS=Toggle"]]
    config.save()
    assert sum(item["commands"] == [GENERAL_TERMINAL_COMMAND] for item in Config(str(tmp_path))["toolbar"]) == 1


def test_previous_button_becomes_the_general_terminal_and_keeps_its_place(tmp_path):
    old = {"label": "Panel de terminal", "icon": "terminalpane", "commands": [TERMINAL_TOGGLE_COMMAND], "tip": "x"}
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 8, "toolbar": [{"label": "Terminal", "commands": [TERMINAL_COMMAND]}, {"label": "Otro", "commands": ["Go UP"]}, old],
        "bar_order": {"toolbar": ["Panel de terminal", "Otro", "Terminal"]}, "bar_buttons": {"toolbar": ["Panel de terminal"]}}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert [item["commands"][0] for item in config["toolbar"]] == [TERMINAL_COMMAND, "Go UP", GENERAL_TERMINAL_COMMAND, "Set PANEFRAMES=Toggle", "Compare FILES", "Set GENERATIONMARKS=Toggle"]
    assert config["toolbar"][2]["label"] == "Terminal general" and config["toolbar"][2]["icon"] == "terminalgeneral"
    assert config["bar_order"]["toolbar"] == ["Terminal general", "Otro", "Terminal"]
    assert config["bar_buttons"]["toolbar"] == ["Terminal general"]


def test_a_button_made_by_the_user_for_the_active_pane_stays(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"config_version": 8, "toolbar": [{"label": "Mi terminal", "commands": [TERMINAL_TOGGLE_COMMAND]}]}), encoding="utf-8")
    config = Config(str(tmp_path))
    assert [item["commands"][0] for item in config["toolbar"]] == [TERMINAL_TOGGLE_COMMAND, GENERAL_TERMINAL_COMMAND, "Set PANEFRAMES=Toggle", "Compare FILES", "Set GENERATIONMARKS=Toggle"]


def test_pane_button_can_be_hidden_from_preferences(window):
    window.config["bar_buttons"] = {"pane": ["terminalpane"]}
    view = window.groups[0].current
    view.set_removed_tools(bar_buttons.hidden(window.config, bar_buttons.PANE))
    button = next(button for button, icon in view._tool_buttons if icon == "terminalpane")
    assert button.isHidden()


def test_pane_button_is_listed_with_the_customizable_buttons():
    listed = {bar: buttons for bar, _title, buttons in bar_buttons.bars({})}
    assert ("terminalpane", "Terminal de este panel") in listed["pane"]


def test_what_a_fast_shell_writes_before_being_registered_is_not_lost(qapp_offscreen):
    """La sesión empieza a leer dentro de su constructor, antes de que la terminal anote su número: dash escribe su indicador en ese momento."""
    class Quick(FakeSession):
        def __init__(self, argv, cwd, cols, rows, on_data, on_exit, env=None) -> None:
            super().__init__(argv, cwd, cols, rows, on_data, on_exit, env)
            on_data(b"$ ")

    view = TerminalView(lambda: os.getcwd())
    view.session_factory = Quick
    view._open_session(os.getcwd())
    assert wait_until(lambda: view.screen_.before_cursor() == "$")
    view.dispose()


@pytest.mark.parametrize("platform, policy", [("linux", Qt.ScrollBarPolicy.ScrollBarAlwaysOn), ("win32", Qt.ScrollBarPolicy.ScrollBarAsNeeded)])
def test_the_scroll_bar_is_only_fixed_on_linux(qapp_offscreen, monkeypatch, platform, policy):
    monkeypatch.setattr(sys, "platform", platform)
    view = TerminalView(lambda: os.getcwd())
    assert view.verticalScrollBarPolicy() == policy
    view.close()


@pytest.mark.skipif(sys.platform == "win32", reason="en Windows la barra aparece cuando hace falta")
def test_the_first_history_does_not_narrow_the_terminal(qapp_offscreen, fake_sessions):
    """Si la barra apareciese entonces, las líneas ya escritas perderían el final."""
    view = TerminalView(lambda: os.getcwd())
    view.resize(500, 200)
    view.show()
    assert wait_until(lambda: view.session is not None)
    columns = view.screen_.cols
    view.screen_.feed(b"x\r\n" * 100)
    view._refresh()
    QApplication.processEvents()
    assert view.screen_.cols == columns
    view.dispose()
    view.close()


def test_programs_started_by_the_packaged_program_do_not_get_its_libraries():
    env = {"LD_LIBRARY_PATH": "/empaquetado", "A": "b"}
    restore_library_path(env, True)
    assert env == {"A": "b"}
    env = {"LD_LIBRARY_PATH": "/empaquetado", "LD_LIBRARY_PATH_ORIG": "/original"}
    restore_library_path(env, True)
    assert env == {"LD_LIBRARY_PATH": "/original"}
    env = {"LD_LIBRARY_PATH": "/del/usuario"}
    restore_library_path(env, False)  # desde el código, o ya restaurada: es la del usuario y se queda
    assert env == {"LD_LIBRARY_PATH": "/del/usuario"}


# ------------------------------------------------------------------ paneles solo de terminal
def only_terminal(window, side: int) -> None:
    """Con el botón de la barra del panel, como lo haría el usuario."""
    window.groups[side].current._bar_tools()["terminalonly"].click()
    settle()


def test_a_pane_can_show_only_its_terminal(window, folders, fake_sessions):
    only_terminal(window, 1)
    assert window._terminal_only.keys() == {1}
    assert window.groups[1].isHidden() and not window.terminal_panels[1].isHidden()
    assert window.terminal_panels[1].height() >= window.pane_areas[1].height() - 2  # ocupa todo el sitio del panel
    assert len(fake_sessions) == 1 and same(fake_sessions[0].cwd, folders[1])  # empieza en la carpeta que tenía
    assert window.active_side == 0 and window.dest is None
    assert window.focusWidget() is window.terminals[1]
    assert window.terminal_files_buttons[1].isVisibleTo(window) and not window.terminal_files_buttons[0].isVisibleTo(window)
    assert window.terminal_close_buttons[1].toolTip() == "Cerrar este panel"
    assert not window.terminals[1].following and window.terminal_follow_buttons[1].toolTip() == "Seguir al panel activo"


def test_clicking_a_terminal_only_pane_does_not_make_it_the_source(window):
    only_terminal(window, 1)
    window.terminals[1].focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
    window.on_tab_changed(window.groups[1])
    assert window.active_side == 0 and window.source is window.groups[0].current


def test_the_last_pane_with_files_cannot_show_only_its_terminal(window):
    only_terminal(window, 1)
    window.run_command("Set TERMINALPANE=On")
    assert window._terminal_only.keys() == {1} and not window.groups[0].isHidden()
    window.apply_style("Explorador")
    assert window._file_panes() == [0]
    assert window.groups[0].current._bar_tools()["terminalonly"].isHidden()  # con un panel ni se ofrece


def test_with_more_panes_the_destination_skips_terminal_only_panes(window):
    window.set_dual("3-Vertical")
    for side in (2, 1, 0):
        window._view_activated(window.groups[side].current)
    window._view_activated(window.groups[1].current)
    assert window._dest_side == 0
    window._set_terminal_only(0, True)
    assert window.active_side == 1 and window._dest_side == 2


def test_the_source_becomes_the_destination_pane(window):
    window.set_dual("3-Vertical")
    window._view_activated(window.groups[2].current)
    window._view_activated(window.groups[1].current)
    window.run_command("Set TERMINALPANE=On")
    assert window._terminal_only.keys() == {1} and window.active_side == 2 and window._dest_side == 0


def test_its_terminal_follows_the_active_pane_only_when_asked(window, folders, tmp_path):
    only_terminal(window, 1)
    terminal, session = window.terminals[1], FakeSession.instances[-1]
    terminal.screen_.feed_text("C:\\>")
    terminal._last_activity = 0
    other = tmp_path / "otra"
    other.mkdir()
    window.groups[0].current.navigate(str(other))
    QTest.qWait(320)
    assert session.sent == b""
    window.terminal_follow_buttons[1].click()
    assert wait_until(lambda: session.sent)
    assert session.sent == cd_command(str(other), terminal.family).encode()


def test_the_files_button_brings_back_the_pane_as_it_was(window, folders):
    window.groups[1].current.activated.emit(window.groups[1].current)
    window.run_command("Set PANETREE=On")
    only_terminal(window, 1)
    assert window.tree_panels[1].isHidden()
    window.terminal_files_buttons[1].click()
    settle()
    assert window._terminal_only == {}
    assert not window.groups[1].isHidden() and not window.tree_panels[1].isHidden() and not window.terminal_panels[1].isHidden()
    assert window.groups[1].height() > 100  # la lista vuelve a tener su sitio junto a la terminal
    assert window.terminals[1].following and window.terminal_close_buttons[1].toolTip() == "Ocultar"
    assert not window.terminal_files_buttons[1].isVisibleTo(window)
    assert window.active_side == 1


def test_the_files_come_back_in_the_folder_the_terminal_went_to(window, folders, tmp_path):
    only_terminal(window, 1)
    terminal, session = window.terminals[1], FakeSession.instances[-1]
    other = tmp_path / "otra"
    other.mkdir()
    session.directory = str(other)  # el usuario se movió con cd
    terminal.screen_.feed_text(f"PS {other}> ")  # PowerShell no se lo dice al sistema: se lee de su indicador
    terminal._last_activity = 0
    window.terminal_files_buttons[1].click()
    settle()
    assert same(window.groups[1].current.path, other)
    QTest.qWait(320)
    assert session.sent == b""  # la terminal no vuelve atrás con cd
    window.groups[1].current.navigate(str(folders[1]))
    assert wait_until(lambda: session.sent)  # y desde ahí sigue a su panel, como antes
    assert session.sent == cd_command(str(folders[1]), terminal.family).encode()


def test_set_terminalpane_off_brings_back_every_pane(window):
    window.set_dual("3-Vertical")
    window._set_terminal_only(1, True)
    window._set_terminal_only(2, True)
    window.run_command("Set TERMINALPANE=Off")
    assert window._terminal_only == {} and not any(group.isHidden() for group in window.groups[:3])
    with pytest.raises(ValueError):
        window._set_terminalpane("quizá")


def test_its_x_closes_the_pane(window, fake_sessions):
    window.set_dual("3-Vertical")
    window._set_terminal_only(2, True)
    settle()
    window.terminal_close_buttons[2].click()
    settle()
    assert window.dual == "Vertical" and window._terminal_only == {}
    assert fake_sessions[0].closed and window.terminal_panels[2].isHidden()
    assert not window.groups[0].isHidden() and not window.groups[1].isHidden()


def test_closing_a_files_pane_keeps_the_terminal_only_one(window):
    window.set_dual("3-Vertical")
    window._set_terminal_only(2, True)
    window._view_activated(window.groups[0].current)
    window.close_pane()
    settle()
    assert window.dual == "Vertical" and window._terminal_only.keys() == {1}
    assert window.active_side == 0 and window.groups[1].isHidden() and not window.terminal_panels[1].isHidden()


def test_when_only_terminal_panes_would_be_left_the_first_gets_its_files_back(window):
    window._set_terminal_only(1, True)
    window._view_activated(window.groups[1].current)  # no puede ser el activo
    assert window.active_side == 0
    window.close_pane()
    settle()
    assert window.dual == "Off" and window._terminal_only == {} and not window.groups[0].isHidden()
    assert not window.terminal_panels[0].isHidden()  # su terminal sigue abierta, ahora junto a sus archivos


def test_a_new_pane_never_starts_as_terminal_only(window):
    window.set_dual("3-Vertical")
    window._set_terminal_only(2, True)
    window.set_dual("Vertical")
    assert window._terminal_only.keys() == {2}  # fuera de la disposición, se conserva como estaba
    window.split_pane("right")
    settle()
    assert window.dual != "Vertical" and window._terminal_only == {} and not window.groups[2].isHidden()


def test_the_exit_of_its_shell_brings_back_the_files(window, fake_sessions):
    only_terminal(window, 1)
    fake_sessions[0].on_exit()
    settle()
    assert window._terminal_only == {} and not window.groups[1].isHidden() and window.terminal_panels[1].isHidden()


def test_the_session_remembers_terminal_only_panes(window, fake_sessions):
    window.set_dual("3-Vertical")
    window._view_activated(window.groups[1].current)
    window.run_command("Set PANETREE=On")
    window._set_terminal_only(1, True)
    window.terminal_follow_buttons[1].click()
    snapshot = window._session_snapshot()
    assert snapshot["terminal_only"] == [{"pane": 1, "follow": True, "tree": True}]
    assert snapshot["layout"]["terminal_panes"] == ["second"]
    window.run_command("Set TERMINALPANE=Off")
    window._apply_session(snapshot, [], live=True)
    settle()
    assert window._terminal_only == {1: (True, True)} and window.groups[1].isHidden() and not window.terminal_panels[1].isHidden()
    assert window.terminals[1].following and window.active_side != 1
    window._apply_session({**snapshot, "terminal_only": []}, [], live=True)
    assert window._terminal_only == {}


def test_a_style_can_ask_for_terminal_only_panes(window):
    window.config["styles"].append({"name": "Con terminal", "dual": "3-Vertical", "tree": False, "terminals": [], "terminal_panes": ["third"]})
    window.apply_style("Con terminal")
    settle()
    assert window._terminal_only.keys() == {2} and not window.terminal_panels[2].isHidden() and window.groups[2].isHidden()
    window.apply_style("Vertical doble panel")  # no dice nada: vuelven los archivos de los que puso así el estilo
    assert window._terminal_only == {}
    window._set_terminal_only(1, True)
    window.apply_style("Vertical doble panel")
    assert window._terminal_only.keys() == {1}  # el puesto a mano se queda
    assert window.current_style_definition("Copia")["terminal_panes"] == ["second"]


def test_a_style_cannot_leave_the_window_without_files(window):
    window.config["styles"].append({"name": "Sin archivos", "dual": "Vertical", "tree": False, "terminal_panes": ["first", "second"]})
    window.apply_style("Sin archivos")
    assert window._terminal_only.keys() == {1} and window.active_side == 0


def test_the_folder_menu_offers_it_only_with_another_pane_with_files(window):
    def entry():
        menu = window.build_folder_menu(window.source)
        return next(action for action in menu.actions() if action.text() == "Solo la terminal en este panel")

    assert entry().isEnabled()
    window._set_terminal_only(1, True)
    assert not entry().isEnabled()


def test_dropped_files_are_written_in_the_terminal(window, folders, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    terminal, session = open_terminal(window)
    named = tmp_path / "con espacio.txt"
    named.write_text("x")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(named)), QUrl.fromLocalFile(str(folders[1]))])
    expected = " ".join(shells_quote(path, terminal.family) for path in (named, folders[1])) + " "
    assert terminal.dropped_text(mime) == expected
    session.sent = b""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    actions = Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
    enter = QDragEnterEvent(QPoint(5, 5), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
    assert terminal.viewport().acceptDrops()  # Qt da el arrastre a la zona de dentro: la terminal en sí no atiende esos eventos
    QApplication.sendEvent(terminal.viewport(), enter)
    assert enter.isAccepted() and enter.dropAction() == Qt.DropAction.CopyAction
    event = QDropEvent(QPointF(5, 5), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
    QApplication.sendEvent(terminal.viewport(), event)
    assert session.sent == expected.encode() and event.dropAction() == Qt.DropAction.CopyAction  # nunca mover, aunque se pida con Mayús
    assert terminal.session is not None and not session.sent.endswith(b"\r")  # escribe, no ejecuta
    text_only = QMimeData()
    text_only.setText("rm -rf /")
    assert terminal.dropped_text(text_only) is None
    terminal.dispose()
    assert terminal.dropped_text(mime) is None


def shells_quote(path, family):
    from directory_commander_tux_tux.core.git_tools import quote
    return quote(os.path.normpath(str(path)), family)
