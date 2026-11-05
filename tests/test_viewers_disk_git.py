"""Visor de código, Markdown y JSON; editor de Markdown; mapa de uso del disco; marcas de git."""
import json
import os
import shutil
import subprocess

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMessageBox

from directory_commander_tux_tux.core import disk_usage, git_tools
from directory_commander_tux_tux.core.listing import entry_from_path
from directory_commander_tux_tux.ui import theme
from directory_commander_tux_tux.ui.code_view import JsonTree, lexer_for, tokenize
from directory_commander_tux_tux.ui.file_view import FileView, row_badges
from directory_commander_tux_tux.ui.model import GitRole
from tests.test_ui import app, select, tree, wait_until, window  # noqa: F401 - se usan como fixtures


# ------------------------------------------------------------------ código, Markdown y JSON en el visor
def test_tokens_are_split_by_line(app):
    lines = tokenize(lexer_for("a.py"), 'def f():\n    """uno\ndos"""\n    return 1\n')
    kinds = {line: [str(kind) for _col, _len, kind in pieces] for line, pieces in lines.items()}
    assert kinds[0][0] == "Token.Keyword" and "Token.Name.Function" in kinds[0]
    assert lines[1][0][:2] == (4, 6) and lines[2][0][:2] == (0, 6)  # la cadena de dos líneas, repartida entre ellas
    assert lexer_for("sin-extension", "#!/bin/bash\necho hola\n") is not None and lexer_for("nada.xyz", "texto") is None


def _preview(window, path):
    window.preview.show_entry(entry_from_path(str(path)))
    assert wait_until(lambda: window.preview._entry_path == str(path) and window.preview.stack.currentWidget() is not window.preview.info)
    return window.preview


def test_viewer_shows_code_with_colors(window, tmp_path):
    (tmp_path / "ejemplo.py").write_text("import os\nprint(os.sep)\n", encoding="utf-8")
    preview = _preview(window, tmp_path / "ejemplo.py")
    assert preview.stack.currentWidget() is preview.text and preview.text.toPlainText().startswith("import os")
    assert preview.highlighter.lexer is not None and not preview.modes.isVisible()
    assert wait_until(lambda: preview.highlighter._lines.get(0))


def test_viewer_shows_markdown_formatted_or_as_code_and_offers_the_editor(window, tmp_path):
    (tmp_path / "LEEME.md").write_text("# Título\n\nTexto con **negrita**.\n", encoding="utf-8")
    preview = _preview(window, tmp_path / "LEEME.md")
    assert preview.stack.currentWidget() is preview.markdown and "Título" in preview.markdown.toPlainText() and "#" not in preview.markdown.toPlainText()
    assert preview.mode_buttons["source"].text() == "Código" and not preview.edit_button.isHidden()
    preview.mode_buttons["source"].click()
    assert preview.stack.currentWidget() is preview.text and preview.text.toPlainText().startswith("# Título")
    asked = []
    preview.editRequested.connect(asked.append)
    preview.edit_button.click()
    assert asked == [str(tmp_path / "LEEME.md")]


def test_viewer_shows_json_formatted_as_a_tree_or_as_it_is(window, tmp_path):
    data = {"nombre": "Ana", "hijos": [{"nombre": "Luis"}, {"nombre": "Eva"}], "activo": True}
    (tmp_path / "datos.json").write_text(json.dumps(data), encoding="utf-8")
    preview = _preview(window, tmp_path / "datos.json")
    assert preview.stack.currentWidget() is preview.text and preview.text.toPlainText() == json.dumps(data, indent=2, ensure_ascii=False)
    preview.mode_buttons["tree"].click()
    assert preview.stack.currentWidget() is preview.json_tree
    root = preview.json_tree.topLevelItem(0)
    assert root.text(0) == "JSON" and preview.json_tree.item_texts(root) == ['nombre : "Ana"', "hijos", "activo : true"]
    hijos = root.child(1)
    assert hijos.childCount() == 0 and hijos.toolTip(0) == "lista · 2 elementos"
    hijos.setExpanded(True)  # los hijos se crean al desplegar
    assert preview.json_tree.item_texts(hijos) == ["0", "1"]
    preview.json_tree.grab()  # con sus líneas y cuadros
    preview.mode_buttons["source"].click()
    assert preview.text.toPlainText() == json.dumps(data)
    (tmp_path / "roto.json").write_text("{nombre: 1", encoding="utf-8")
    preview = _preview(window, tmp_path / "roto.json")
    assert preview.stack.currentWidget() is preview.text and "No es un JSON válido" in preview.title.text() and not preview.modes.isVisible()


def test_json_tree_limits_huge_lists(app):
    tree_widget = JsonTree()
    tree_widget.show_data(list(range(2500)))
    root = tree_widget.topLevelItem(0)
    root.setExpanded(True)
    assert root.childCount() == 2001 and root.child(2000).text(0) == "… y 500 más"


# ------------------------------------------------------------------ editor de Markdown
def test_markdown_editor_saves_with_the_same_line_endings(app, tmp_path, monkeypatch):
    from directory_commander_tux_tux.ui.markdown_editor import MarkdownEditor

    path = tmp_path / "notas.md"
    path.write_bytes("# Notas\r\n\r\nuno\r\n".encode("utf-8"))
    editor = MarkdownEditor(str(path))
    editor.show()
    assert "Notas" in editor.preview.toPlainText() and not editor.windowTitle().startswith("*")
    editor.editor.appendPlainText("dos")
    assert editor.windowTitle().startswith("*")
    assert wait_until(lambda: "dos" in editor.preview.toPlainText())  # la vista sigue al texto
    assert editor.save()
    assert path.read_bytes() == "# Notas\r\n\r\nuno\r\n\r\ndos".encode("utf-8") and not editor.windowTitle().startswith("*")
    cursor = editor.editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    editor.editor.setTextCursor(cursor)
    editor.prefix_lines("- ")
    editor.wrap_selection("**", "**")
    assert editor.editor.toPlainText().endswith("- dos****")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: asked.append(args[2]) or QMessageBox.StandardButton.Cancel)
    editor.close()
    assert asked and editor.isVisible()  # cambios sin guardar: se pregunta, y Cancelar la deja abierta
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
    editor.close()


def test_markdown_editor_refuses_other_encodings(app, tmp_path):
    from directory_commander_tux_tux.ui.markdown_editor import MarkdownEditor

    (tmp_path / "latin.md").write_bytes("canción".encode("latin-1"))
    with pytest.raises(UnicodeDecodeError):
        MarkdownEditor(str(tmp_path / "latin.md"))


def test_opening_markdown_uses_the_editor(window, tree):
    from directory_commander_tux_tux.ui.markdown_editor import MarkdownEditor
    from tests.test_ui import labels

    left, _right = tree
    (left / "LEEME.md").write_text("# hola\n", encoding="utf-8")
    view = window.source
    view.refresh()
    assert wait_until(lambda: any(e.name == "LEEME.md" for e in view.model.entries))
    select(view, "LEEME.md")
    assert "Editar Markdown" in labels(window.build_file_menu(view))
    entry = next(e for e in view.model.entries if e.name == "LEEME.md")
    window._open_file(view, entry)
    editors = window.findChildren(MarkdownEditor)
    assert len(editors) == 1 and editors[0].path == str(left / "LEEME.md")
    window._open_file(view, entry)
    assert len(window.findChildren(MarkdownEditor)) == 1  # el mismo archivo no se abre dos veces
    editors[0].close()


# ------------------------------------------------------------------ uso del disco
def test_disk_usage_scan_sums_folders_and_counts_hard_links_once(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "grande.bin").write_bytes(b"x" * 5000)
    (tmp_path / "a" / "b" / "chico.txt").write_bytes(b"x" * 100)
    (tmp_path / "suelto.txt").write_bytes(b"x" * 10)
    os.link(tmp_path / "a" / "grande.bin", tmp_path / "enlace.bin")
    top = disk_usage.scan(str(tmp_path))
    assert top.size == 5110 and top.files == 3
    names = {child.name for child in top.children}
    linked = {"enlace.bin", "grande.bin"} & (names | {child.name for child in disk_usage.find(top, str(tmp_path / "a")).children})
    assert len(linked) == 1  # los dos nombres del mismo archivo: solo cuenta el primero que se encuentra
    folder = disk_usage.find(top, str(tmp_path / "a"))
    assert folder.size in (100, 5100) and [child.size for child in folder.children] == sorted((child.size for child in folder.children), reverse=True)
    found = disk_usage.find(top, str(tmp_path / "a" / "b" / "chico.txt"))
    assert found is not None and found.size == 100 and found.path == str(tmp_path / "a" / "b" / "chico.txt")
    assert [node.name for node in disk_usage.path_to(found)] == [tmp_path.name, "a", "b", "chico.txt"]


def test_squarify_fills_the_rectangle_in_proportion():
    values = [600.0, 300.0, 100.0]
    places = disk_usage.squarify(values, 0, 0, 100, 50)
    areas = [width * height for _x, _y, width, height in places]
    assert [round(area) for area in areas] == [3000, 1500, 500]
    assert all(0 <= x and 0 <= y and x + width <= 100.001 and y + height <= 50.001 for x, y, width, height in places)
    assert disk_usage.squarify([0.0, 0.0], 0, 0, 10, 10) == [(0, 0, 0.0, 0.0), (0, 0, 0.0, 0.0)]


def test_disk_usage_window_links_the_list_and_the_map(app, tmp_path):
    from directory_commander_tux_tux.ui.disk_usage import DiskUsageWindow

    (tmp_path / "fotos").mkdir()
    for number in range(4):
        (tmp_path / "fotos" / f"{number}.png").write_bytes(b"x" * (1000 * (number + 1)))
    (tmp_path / "video.mp4").write_bytes(b"x" * 20000)
    shown = []
    window_ = DiskUsageWindow(str(tmp_path), lambda folder, selected: shown.append((folder, selected)))
    window_.resize(800, 600)
    window_.show()
    assert wait_until(lambda: window_.top is not None)
    window_.map.repaint()
    video = window_.top.children[0]
    assert video.name == "video.mp4"
    place = window_.map.place_of(video)
    assert place is not None and window_.map.node_at(place.center()) is video
    window_.map.nodeChosen.emit(video)  # un clic en el mapa marca la fila
    assert window_.tree.currentItem().data(0, 0x0101) is video
    fotos = window_.top.children[1]
    window_.zoom(fotos)
    window_.map.repaint()
    assert window_.map.root is fotos and window_.up_action.isEnabled()
    assert window_.map.node_at(QPointF(5, 5)) in fotos.children
    window_.zoom_out()
    assert window_.map.root is window_.top
    window_._open(video)
    assert shown == [(str(tmp_path), str(tmp_path / "video.mp4"))]
    window_.close()


def test_disk_usage_command_opens_the_selected_folder(window, tree):
    from directory_commander_tux_tux.ui.disk_usage import DiskUsageWindow

    left, _right = tree
    select(window.source, "subcarpeta")
    window.run_command("DiskUsage")
    opened = window.findChildren(DiskUsageWindow)
    assert opened and opened[-1].path == str(left / "subcarpeta")
    assert wait_until(lambda: opened[-1].top is not None and opened[-1].top.files == 1)
    opened[-1].close()


# ------------------------------------------------------------------ marcas de git
@pytest.fixture
def repository(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("sin git")
    folder = tmp_path / "repo"
    (folder / "src").mkdir(parents=True)
    (folder / "venv").mkdir()
    (folder / "src" / "a.py").write_text("a\n", encoding="utf-8")
    (folder / "b.txt").write_text("b\n", encoding="utf-8")

    def git(*arguments):
        subprocess.run(["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@t", *arguments], check=True, capture_output=True)

    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "inicio")
    (folder / "src" / "a.py").write_text("cambiado\n", encoding="utf-8")
    (folder / "nuevo.txt").write_text("n\n", encoding="utf-8")
    (folder / ".gitignore").write_text("venv/\n", encoding="utf-8")
    (folder / "venv" / "x").write_text("", encoding="utf-8")
    git("add", ".gitignore")
    return folder


def test_git_states_of_a_folder(repository):
    files, folders, ignored = git_tools.folder_states(git_tools.git_program(), str(repository))
    state = lambda name: git_tools.state_of(str(repository / name), files, folders, ignored)  # noqa: E731
    assert state("src/a.py") == ("M", False) and state("src") == ("M", True)
    assert state("nuevo.txt") == ("?", False) and state(".gitignore") == ("A", False)
    assert state("venv") == ("!", False) and state("venv/x") == ("!", False) and state("b.txt") is None
    (repository / "fotos").mkdir()
    (repository / "fotos" / "a.jpg").write_bytes(b"x")
    files, folders, whole = git_tools.folder_states(git_tools.git_program(), str(repository))
    assert git_tools.state_of(str(repository / "fotos" / "a.jpg"), files, folders, whole) == ("?", False)  # dentro de una carpeta sin seguimiento
    assert git_tools.folder_states(git_tools.git_program(), str(repository.parent)) == ({}, {}, ())  # fuera de un repositorio


def test_list_shows_git_marks_and_can_hide_them(app, repository):
    view = FileView()
    view.resize(700, 300)
    view.show()
    view.navigate(str(repository))
    assert wait_until(lambda: view.model.rowCount() == 4)  # .git y .gitignore, ocultos
    index = {view.model.entry(row).name: view.model.index(row, 0) for row in range(view.model.rowCount())}
    assert wait_until(lambda: index["src"].data(GitRole) == ("M", True))
    assert row_badges(index["src"]) == [("•", "#d9822b")] and row_badges(index["nuevo.txt"]) == [("U", "#3fa36b")]
    assert row_badges(index["b.txt"]) == [] and row_badges(index["venv"]) == []
    assert index["venv"].data(Qt.ItemDataRole.ForegroundRole) == QColor(theme.current().dim)  # lo ignorado, más tenue
    assert index["src"].data(Qt.ItemDataRole.ForegroundRole) == QColor(theme.current().dir_text)
    view.grab()
    view.set_git_marks(False)
    assert index["src"].data(GitRole) is None
    view.dispose()


# ------------------------------------------------------------------ Markdown con HTML, ventana de JSON, borrar desde el mapa y tooltips de git
def test_markdown_with_unclosed_html_keeps_its_text(app):
    from PySide6.QtGui import QTextDocument
    from directory_commander_tux_tux.ui.code_view import markdown_for_qt

    source = "a | b\n--|--\nuno<br>dos | x\n\n- texto normal con `código` y más\n\n```\n<br>\n```\n"
    fixed = markdown_for_qt(source)
    assert "uno<br/>dos" in fixed and "```\n<br>\n```" in fixed and "`código`" in fixed  # el código, tal cual
    document = QTextDocument()
    document.setMarkdown(fixed)
    assert "texto normal con código y más" in document.toPlainText()  # sin el arreglo, Qt dejaba solo «código»


def test_json_window_searches_and_copies_paths(app, tmp_path):
    from directory_commander_tux_tux.ui.code_view import json_path
    from directory_commander_tux_tux.ui.json_viewer import JsonViewer

    data = {"pages": [{"id": "p1", "nodes": [{"id": "c-title", "type": "title"}, {"id": "f-tipo", "type": "radio"}]}]}
    (tmp_path / "datos.json").write_text(json.dumps(data), encoding="utf-8")
    viewer = JsonViewer(str(tmp_path / "datos.json"))
    viewer.show()
    viewer.search.setText("radio")
    assert viewer.find_next() and viewer.tree.currentItem().text(0) == 'type : "radio"'
    path = viewer.tree.currentItem().data(0, viewer.tree.PATH_ROLE)
    assert json_path(path) == "pages[0].nodes[1].type" and viewer.value_at(path) == "radio"
    viewer.show_mode("formatted")
    assert viewer.text.toPlainText() == json.dumps(data, indent=2)
    viewer.show_mode("source")
    assert viewer.text.toPlainText() == json.dumps(data)
    viewer.search.setText("no-está")
    assert not viewer.find_next()
    viewer.close()
    (tmp_path / "roto.json").write_text("{a:", encoding="utf-8")
    with pytest.raises(ValueError):
        JsonViewer(str(tmp_path / "roto.json"))


def test_opening_json_uses_its_window(window, tree):
    from directory_commander_tux_tux.ui.json_viewer import JsonViewer
    from tests.test_ui import labels

    left, _right = tree
    (left / "datos.json").write_text('{"a": 1}', encoding="utf-8")
    view = window.source
    view.refresh()
    assert wait_until(lambda: any(e.name == "datos.json" for e in view.model.entries))
    select(view, "datos.json")
    assert "Ver JSON" in labels(window.build_file_menu(view))
    window._open_file(view, next(e for e in view.model.entries if e.name == "datos.json"))
    opened = window.findChildren(JsonViewer)
    assert len(opened) == 1 and opened[0].path == str(left / "datos.json")
    opened[0].close()


def test_deleting_from_the_disk_map_updates_it(app, tmp_path, monkeypatch):
    from directory_commander_tux_tux.ui.disk_usage import DiskUsageWindow

    (tmp_path / "fotos").mkdir()
    (tmp_path / "fotos" / "a.png").write_bytes(b"x" * 3000)
    (tmp_path / "fotos" / "b.png").write_bytes(b"x" * 1000)
    (tmp_path / "video.mp4").write_bytes(b"x" * 20000)
    asked = []

    def delete(paths, permanent, done, parent=None, detail=""):
        asked.append((paths, permanent, detail))
        for path in paths:
            os.remove(path)
        done(None)
        return True

    window_ = DiskUsageWindow(str(tmp_path), None, delete=delete)
    window_.show()
    assert wait_until(lambda: window_.top is not None)
    fotos = disk_usage.find(window_.top, str(tmp_path / "fotos"))
    big = disk_usage.find(window_.top, str(tmp_path / "fotos" / "a.png"))
    window_.select_node(big)
    window_.delete_node(big, False)
    assert asked == [([str(tmp_path / "fotos" / "a.png")], False, " (2.93 KB)")]
    assert window_.top.size == 21000 and window_.top.files == 2 and fotos.size == 1000 and fotos.files == 1
    assert [child.name for child in fotos.children] == ["b.png"] and window_.item_for(fotos).text(2) == "1000 B"
    window_.map.repaint()
    assert window_.map.place_of(big) is None
    window_.delete_node(window_.top, True)  # la carpeta analizada, no
    assert len(asked) == 1
    window_.close()


def test_git_marks_explain_themselves_and_ai_marks_do_not(app, repository):
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QToolTip
    from directory_commander_tux_tux.ui.file_view import badge_meaning_at

    view = FileView()
    view.resize(700, 300)
    view.show()
    view.navigate(str(repository))
    assert wait_until(lambda: view.model.rowCount() == 4)
    index = {view.model.entry(row).name: view.model.index(row, 0) for row in range(view.model.rowCount())}
    assert wait_until(lambda: index["src"].data(GitRole) == ("M", True))
    view.details.viewport().repaint()
    spots = view.details.badge_spots
    meaning = lambda name: [text for _place, text in spots.get(index[name].row(), [])]  # noqa: E731
    assert meaning("src") == ["Git: hay cambios dentro. El más importante: Modificado"]
    assert meaning("nuevo.txt") == ["Git: Sin seguimiento: nuevo, git aún no lo guarda"]
    place = spots[index["src"].row()][0][0]
    assert badge_meaning_at(view.details, index["src"].row(), place.center().toPoint()) == meaning("src")[0]
    assert badge_meaning_at(view.details, index["src"].row(), QPoint(1, 1)) == ""
    # La descripción se va en cuanto el ratón sale de la marca, aunque siga sobre la lista.
    viewport = view.details.viewport()
    inside = place.center().toPoint()
    QApplication.sendEvent(viewport, QHelpEvent(QEvent.Type.ToolTip, inside, viewport.mapToGlobal(inside)))
    assert wait_until(lambda: QToolTip.isVisible() and QToolTip.text() == meaning("src")[0])
    QTest.mouseMove(viewport, QPoint(int(place.right()) + 150, viewport.height() - 10))  # a la parte vacía, bajo las filas
    QTest.qWait(500)  # Qt la quita a los 300 ms; antes de que el reposo pida otra descripción
    assert not QToolTip.isVisible()
    view.dispose()


def test_disk_map_opens_files_and_offers_both_deletes(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QToolTip
    from directory_commander_tux_tux.ui import disk_usage as module
    from directory_commander_tux_tux.ui.disk_usage import DiskUsageWindow

    (tmp_path / "carpeta").mkdir()
    (tmp_path / "carpeta" / "uno.txt").write_bytes(b"x" * 4000)
    (tmp_path / "dos.txt").write_bytes(b"x" * 9000)
    opened = []
    window_ = DiskUsageWindow(str(tmp_path), None, delete=lambda *args, **kwargs: True, open_file=opened.append)
    window_.resize(600, 500)
    window_.show()
    assert wait_until(lambda: window_.top is not None)
    two = disk_usage.find(window_.top, str(tmp_path / "dos.txt"))
    window_.activate(two)  # doble clic en la lista
    window_.activate(disk_usage.find(window_.top, str(tmp_path / "carpeta")))  # una carpeta no se abre: se despliega
    assert opened == [str(tmp_path / "dos.txt")]
    shown = [(a.text(), a.shortcut().toString()) for a in window_.build_menu(two).actions() if a.text()]
    assert ("Abrir", "") in shown and ("Enviar a la papelera", "Del") in shown
    assert ("Eliminar definitivamente, sin papelera", "Shift+Del") in shown
    window_.map.repaint()
    tips = []
    monkeypatch.setattr(module.QToolTip, "showText", staticmethod(lambda pos, text, widget, rect=None: tips.append((text, rect))))
    center = window_.map.place_of(two).center().toPoint()
    window_.map.event(QHelpEvent(QEvent.Type.ToolTip, center, window_.map.mapToGlobal(center)))
    assert tips and tips[0][0].startswith(str(tmp_path / "dos.txt"))
    assert tips[0][1] == window_.map.place_of(two).toAlignedRect()  # se queda mientras el ratón no salga del cuadro
    window_.close()


# ------------------------------------------------------------------ Markdown sin recargas, como GitHub; orden de serie; git en su página
def test_coloring_does_not_trigger_itself(app):
    import time
    from PySide6.QtWidgets import QPlainTextEdit
    from directory_commander_tux_tux.ui.code_view import CodeHighlighter

    editor = QPlainTextEdit()
    editor.setPlainText("# hola\n\ntexto `x`\n" * 30)
    highlighter = CodeHighlighter(editor.document())
    runs = []
    original = highlighter._tokenize
    highlighter._tokenize = lambda: (runs.append(1), original())
    highlighter.set_lexer(lexer_for("x.md"))
    end = time.time() + 1.5
    while time.time() < end:
        app.processEvents()
    assert len(runs) == 1  # colorear no cuenta como cambiar el texto: antes se repetía cada 350 ms sin fin
    editor.insertPlainText("más")
    assert wait_until(lambda: len(runs) == 2)


def test_markdown_view_fits_images_and_styles_code_and_tables(app, tmp_path):
    from PySide6.QtGui import QImage, QTextCursor
    from directory_commander_tux_tux.ui.code_view import MarkdownView

    QImage(1600, 900, QImage.Format.Format_RGB32).save(str(tmp_path / "grande.png"))
    text = "# Título\n\n![captura](grande.png)\n\n| A | B |\n|---|---|\n| ![x](grande.png) | b |\n\n```\ncódigo\n```\n\ncon `suelto` aquí\n"
    view = MarkdownView()
    view.resize(500, 600)
    view.show()
    view.show_markdown(text, str(tmp_path))
    widths = []
    block = view.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            form = fragment.charFormat()
            if form.isImageFormat():
                cursor = QTextCursor(view.document())
                cursor.setPosition(fragment.position())
                widths.append((round(form.toImageFormat().width()), cursor.currentTable() is not None))
            iterator += 1
        block = block.next()
    room = view.viewport().width() - 2 * int(view.document().documentMargin()) - 4
    (alone, outside), (inside, in_table) = widths
    assert not outside and in_table
    assert room - 30 <= alone <= room and inside <= room // 2  # a lo ancho de la vista (con o sin barra de desplazamiento), y en la tabla, de su columna
    assert "código" in view.toPlainText() and "suelto" in view.toPlainText()


def test_new_tabs_sort_by_type_unless_chosen(window, tree):
    left, _right = tree
    (left / "z.md").write_text("", encoding="utf-8")
    view = window.source
    view.refresh()
    assert wait_until(lambda: [e.name for e in view.model.entries] == ["subcarpeta", "z.md", "a.txt", "b.txt"])  # Archivo MD antes que Archivo TXT
    window.config["default_sort"] = "name"
    other = window.groups[window.active_side].add_tab(str(left))
    assert wait_until(lambda: [e.name for e in other.model.entries] == ["subcarpeta", "a.txt", "b.txt", "z.md"])


def test_git_marks_are_set_in_the_git_page(window):
    from directory_commander_tux_tux.ui.dialogs import PreferencesDialog

    dialog = PreferencesDialog(window.config)
    titles = [dialog.pages.tabText(index) for index in range(dialog.pages.count())]
    assert "Integración con Git" in titles and "Git" not in titles
    assert not hasattr(dialog, "git_marks") and dialog.git_page.marks.isChecked()
    dialog.git_page.marks.setChecked(False)
    dialog.apply_to(window.config)
    assert window.config["git_marks"] is False
    dialog.close()


def test_markdown_headings_keep_their_size_and_links_are_followed(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextFormat
    from directory_commander_tux_tux.ui import open_external
    from directory_commander_tux_tux.ui.code_view import MarkdownView, heading_anchor

    (tmp_path / "LEEME.md").write_text("# Uno ~~tachado~~ y más\n\n[English](README.en.md) | [web](https://example.com)\n\n## Estilos: una disposición\n\ntexto\n",
                                       encoding="utf-8")
    (tmp_path / "README.en.md").write_text("a | b\n--|--\nx<br>y | z\n\n- with `code` and text\n", encoding="utf-8")
    view = MarkdownView()
    view.resize(600, 400)
    view.show()
    view.show_file(str(tmp_path / "LEEME.md"))
    block = view.document().begin()
    sizes = []
    iterator = block.begin()
    while not iterator.atEnd():
        form = iterator.fragment().charFormat()
        sizes.append((form.property(QTextFormat.Property.FontSizeAdjustment), form.fontWeight()))
        iterator += 1
    assert len(sizes) == 3 and len(set(sizes)) == 1  # lo de detrás del tachado, del mismo tamaño que el título
    assert heading_anchor("Estilos: una disposición") == "estilos-una-disposición"
    assert view.go_to_heading("estilos-una-disposición") and not view.go_to_heading("no-existe")
    opened = []
    monkeypatch.setattr(open_external, "open_externally", lambda target: opened.append(target) or True)
    view._follow(QUrl("https://example.com"))
    assert opened == ["https://example.com"]
    view._follow(QUrl("README.en.md"))
    assert view.path == str(tmp_path / "README.en.md") and "with code and text" in view.toPlainText()  # con el arreglo del <br>


def test_a_link_to_another_markdown_opens_another_editor(window, tmp_path):
    from PySide6.QtCore import QUrl
    from directory_commander_tux_tux.ui.markdown_editor import MarkdownEditor

    (tmp_path / "uno.md").write_text("[dos](dos.md)\n", encoding="utf-8")
    (tmp_path / "dos.md").write_text("# Dos\n", encoding="utf-8")
    window.open_markdown_editor(str(tmp_path / "uno.md"))
    editor = window.findChildren(MarkdownEditor)[0]
    editor.preview._follow(QUrl("dos.md"))
    paths = sorted(os.path.basename(item.path) for item in window.findChildren(MarkdownEditor))
    assert paths == ["dos.md", "uno.md"] and editor.preview.path != str(tmp_path / "dos.md")  # la vista sigue siendo la del texto que se edita
    for item in window.findChildren(MarkdownEditor):
        item.close()
