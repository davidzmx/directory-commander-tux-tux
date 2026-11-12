"""Pruebas del gestor de notas y fragmentos rápidos (sin Qt)."""
import os
import pytest

from directory_commander_tux_tux.core import notes


def test_create_and_title_extraction():
    note = notes.create_note("Primera línea como título\nSegunda línea")
    assert note.title == "Primera línea como título"
    assert "Segunda línea" in note.content
    assert note.id

    empty_note = notes.create_note("")
    assert empty_note.title == "Nueva nota"


def test_save_and_load_notes(tmp_path):
    n1 = notes.create_note("curl https://api.example.com", "API Call")
    n2 = notes.create_note("/var/log/syslog", "Ruta de logs")
    notes.save_notes(str(tmp_path), [n1, n2])

    loaded = notes.load_notes(str(tmp_path))
    assert len(loaded) == 2
    assert loaded[0].id == n1.id
    assert loaded[0].title == "API Call"
    assert loaded[0].content == "curl https://api.example.com"
    assert loaded[1].id == n2.id
    assert loaded[1].title == "Ruta de logs"


def test_update_and_delete_note(tmp_path):
    n1 = notes.create_note("texto inicial", "Título")
    n2 = notes.create_note("otro texto", "Otro")
    note_list = [n1, n2]

    # Actualizar nota
    assert notes.update_note(note_list, n1.id, "texto cambiado\nnueva línea")
    assert n1.content == "texto cambiado\nnueva línea"
    assert n1.title == "texto cambiado"

    # Eliminar nota
    assert notes.delete_note(note_list, n1.id)
    assert len(note_list) == 1
    assert note_list[0].id == n2.id
    assert not notes.delete_note(note_list, "id_inexistente")


def test_load_notes_empty_or_corrupted(tmp_path):
    # Directorio sin archivo
    assert notes.load_notes(str(tmp_path)) == []

    # Archivo corrupto
    bad_file = tmp_path / notes.NOTES_FILE
    bad_file.write_text("{ esto no es json válido }", encoding="utf-8")
    assert notes.load_notes(str(tmp_path)) == []


def test_notes_popup_actions(tmp_path, qapp_for_gc):
    from directory_commander_tux_tux.ui.notes_popup import NotesPopup

    popup = NotesPopup(str(tmp_path))
    # Al arrancar de vacío crea la nota de bienvenida
    assert len(popup.notes) >= 1
    assert popup.list_widget.count() >= 1

    # Crear una nueva nota
    popup.new_note()
    popup.editor.setPlainText("ssh user@example.com -p 2222\nComando SSH")
    popup._on_text_changed()
    popup._save_current_note()

    assert popup.title_input.text() == "ssh user@example.com -p 2222"
    assert len(popup.notes) >= 2

    # Filtrar notas
    popup.filter_input.setText("ssh")
    assert popup.list_widget.count() == 1

    popup.filter_input.clear()
    assert popup.list_widget.count() >= 2

    # Copiar nota
    popup.copy_current_note()
    assert qapp_for_gc.clipboard().text() == "ssh user@example.com -p 2222\nComando SSH"

    # Eliminar nota
    initial_count = len(popup.notes)
    popup.delete_current_note()
    assert len(popup.notes) == initial_count - 1

    popup.close()


def test_main_window_notes_command_and_toolbar(tmp_path, qapp_for_gc):
    from directory_commander_tux_tux.core.config import Config, NOTES_COMMAND
    from directory_commander_tux_tux.ui.main_window import MainWindow

    cfg = Config(str(tmp_path / "cfg"))
    win = MainWindow(cfg)

    # Verificar que el botón de Notas está en la barra de herramientas
    notes_btn = win._find_notes_button()
    assert notes_btn is not None

    # Ejecutar comando Notes para abrir el popup
    assert not win.notes_popup.isVisible()
    win.run_command("Notes")
    assert win.notes_popup.isVisible()

    # Ejecutar comando Notes de nuevo para cerrar (toggle)
    win.run_command("Notes")
    assert not win.notes_popup.isVisible()

    # Ejecutar vía Set NOTES=Toggle
    win.run_command(NOTES_COMMAND)
    assert win.notes_popup.isVisible()

    win.run_command(NOTES_COMMAND)
    assert not win.notes_popup.isVisible()

    win.close()


def test_notes_export_and_import(tmp_path):
    n1 = notes.create_note("comando 1", "Título 1")
    n2 = notes.create_note("comando 2", "Título 2")
    export_file = tmp_path / "exported_notes.json"

    notes.export_notes_to_file(str(export_file), [n1, n2])
    assert export_file.is_file()

    target_list = []
    added = notes.import_notes_from_file(str(export_file), target_list)
    assert added == 2
    assert len(target_list) == 2
    assert target_list[0].title == "Título 1"
    assert target_list[1].title == "Título 2"

    # Importar archivo de texto plano
    txt_file = tmp_path / "script.sh"
    txt_file.write_text("#!/bin/bash\necho test", encoding="utf-8")
    added_txt = notes.import_notes_from_file(str(txt_file), target_list)
    assert added_txt == 1
    assert len(target_list) == 3
    assert target_list[2].title == "script"
    assert "#!/bin/bash" in target_list[2].content


def test_notes_popup_auto_copy_on_selection(tmp_path, qapp_for_gc):
    from PySide6.QtGui import QTextCursor
    from directory_commander_tux_tux.ui.notes_popup import NotesPopup

    popup = NotesPopup(str(tmp_path))
    popup.editor.setPlainText("Texto para copiar automáticamente\nSegunda línea")
    cursor = popup.editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(16, QTextCursor.MoveMode.KeepAnchor)
    popup.editor.setTextCursor(cursor)

    # Disparar copia de selección
    popup._copy_selected_text()
    assert qapp_for_gc.clipboard().text() == "Texto para copia"
    popup.close()


def test_notes_popup_search_highlighting(tmp_path, qapp_for_gc):
    from directory_commander_tux_tux.ui.notes_popup import NotesPopup

    popup = NotesPopup(str(tmp_path))
    popup.new_note()
    popup.editor.setPlainText("git status\ngit commit -m test\ngit push")
    popup._save_current_note()

    # Buscar coincidencia
    popup.filter_input.setText("git")
    popup._filter_notes()

    # Comprobar que en el editor hay 3 selecciones resaltadas
    selections = popup.editor.extraSelections()
    assert len(selections) == 3

    # Limpiar búsqueda
    popup.filter_input.clear()
    popup._filter_notes()
    assert len(popup.editor.extraSelections()) == 0

    popup.close()


def test_notes_popup_resizable(tmp_path, qapp_for_gc):
    from directory_commander_tux_tux.ui.notes_popup import NotesPopup

    popup = NotesPopup(str(tmp_path))
    # Tamaño inicial
    assert popup.width() >= 440
    assert popup.height() >= 280

    # Redimensionar
    popup.resize(650, 480)
    popup._save_size()
    popup.close()

    # Al volver a crear debe recordar el tamaño guardado
    popup2 = NotesPopup(str(tmp_path))
    assert popup2.width() == 650
    assert popup2.height() == 480
    popup2.close()

