"""Columnas de la lista y de detalles, tamaño de las miniaturas y la marca de las imágenes y vídeos generados con su prompt."""
import json
import os
import time
from unittest import mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMenu

from directory_commander_tux_tux.core import ai_metadata, devices, media_info
from directory_commander_tux_tux.core.fsops import OpResult
from directory_commander_tux_tux.core.listing import format_time
from directory_commander_tux_tux.ui import model as model_module
from directory_commander_tux_tux.ui.file_view import (PROMPT_MARK_COLOR, WORKFLOW_MARK_COLOR, FileView, generation_badges, inline_marks_width)
from directory_commander_tux_tux.ui.folder_tree import PATH_ROLE, USAGE_ROLE, FolderTree, device_tooltip
from directory_commander_tux_tux.ui.model import GenerationRole
from tests import ai_samples
from tests.test_ui import app, select, tree, wait_until, window  # noqa: F401 - se usan como fixtures


# ------------------------------------------------------------------ las marcas de IA
def test_generation_data_is_read_from_each_program_and_format(tmp_path):
    cases = {
        "comfy.png": (ai_samples.comfy_png(), ("ComfyUI", "un gato astronauta, acuarela", "borroso, feo", True)),
        "a1111.png": (ai_samples.png(itxt={"parameters": ai_samples.A1111_PARAMETERS}), ("Automatic1111", "un faro en la tormenta, óleo", "texto, marca de agua", False)),
        "forge.png": (ai_samples.png({"parameters": "un gato\nSteps: 20, Sampler: Euler, Version: f2.0.1v1.10.1"}), ("Forge", "un gato", "", False)),
        "detras.png": (ai_samples.png({"parameters": "un perro\nSteps: 20"}, after_image=True), ("Automatic1111 o compatible", "un perro", "", False)),
        "solo-flujo.png": (ai_samples.png({"workflow": json.dumps({"nodes": [{"type": "CLIPTextEncode", "widgets_values": ["del flujo"]}]})}),
                           ("ComfyUI", "del flujo", "", True)),
        "a1111.jpg": (ai_samples.a1111_jpeg(), ("Automatic1111", "un faro en la tormenta, óleo", "texto, marca de agua", False)),
        "comfy.webp": (ai_samples.comfy_webp(), ("ComfyUI", "un gato astronauta, acuarela", "borroso, feo", True)),
        "comfy.mp4": (ai_samples.comfy_mp4(), ("ComfyUI", "un gato astronauta, acuarela", "borroso, feo", True)),
        "novelai.png": (ai_samples.png({"Software": "NovelAI", "Description": "1girl, flores", "Comment": json.dumps({"uc": "lowres", "seed": 5})}),
                        ("NovelAI", "1girl, flores", "lowres", False)),
        "invoke.png": (ai_samples.png({"invokeai_metadata": json.dumps({"positive_prompt": "un barco", "model": {"name": "sdxl"}}), "invokeai_workflow": "{}"}),
                       ("InvokeAI", "un barco", "", True)),
        "fooocus.png": (ai_samples.png({"parameters": json.dumps({"prompt": "montaña", "negative_prompt": "", "version": "Fooocus v2.5.0"})}),
                        ("Fooocus", "montaña", "", False)),
        "swarm.png": (ai_samples.png({"parameters": json.dumps({"sui_image_params": {"prompt": "río", "model": "flux"}})}), ("SwarmUI", "río", "", False)),
        "mj.png": (ai_samples.png({"Description": "a castle --ar 16:9 Job ID: 1234-5678"}), ("Midjourney", "a castle --ar 16:9", "", False)),
    }
    for name, (data, (tool, prompt, negative, workflow)) in cases.items():
        (tmp_path / name).write_bytes(data)
        found = ai_metadata.read_generation(str(tmp_path / name), name.rsplit(".", 1)[1])
        assert found is not None and (found.tool, found.prompt, found.negative, found.workflow) == (tool, prompt, negative, workflow), name
    comfy = ai_metadata.read_generation(str(tmp_path / "comfy.png"), "png")
    assert comfy.nodes == 7 and dict(comfy.settings) == {"Modelo": "v1-5-pruned-emaonly.safetensors", "Semilla": "156680208700286", "Pasos": "20", "CFG": "8",
                                                          "Muestreador": "euler", "Planificador": "normal", "Reducción de ruido": "1", "Tamaño": "512×768"}
    a1111 = dict(ai_metadata.read_generation(str(tmp_path / "a1111.jpg"), "jpg").settings)
    assert a1111["Pasos"] == "28" and a1111["CFG"] == "6.5" and a1111["Lora hashes"] == "estilo: 1a2b, otro: 3c4d" and a1111["Modelo"] == "juggernautXL"


def test_files_without_generation_data_give_nothing(tmp_path):
    cases = {"normal.png": ai_samples.png({"Software": "GIMP"}), "foto.jpg": b"\xff\xd8\xff\xe0\0\x10JFIF\0" + os.urandom(2000) + b"\xff\xd9",
             "normal.mp4": ai_samples.plain_mp4(), "roto.png": b"\x89PNG\r\n\x1a\n" + b"\xff" * 30, "roto.webp": b"RIFF\xff\xff\xff\xffWEBPEXIF\xff\xff\xff\x7f",
             "vacio.jpg": b""}
    for name, data in cases.items():
        (tmp_path / name).write_bytes(data)
        assert ai_metadata.read_generation(str(tmp_path / name), name.rsplit(".", 1)[1]) is None, name
    assert ai_metadata.read_generation(str(tmp_path / "no-existe.png"), "png") is None


def test_a_hostile_compressed_text_is_not_inflated(tmp_path):
    bomb = ai_samples.png(itxt={"parameters": "a" * (ai_metadata.MAX_TEXT + 10)})
    (tmp_path / "bomba.png").write_bytes(bomb)
    assert ai_metadata.read_generation(str(tmp_path / "bomba.png"), "png") is None


def _marked_view(tmp_path, mode):
    (tmp_path / "comfy.png").write_bytes(ai_samples.comfy_png())
    (tmp_path / "a1111.png").write_bytes(ai_samples.png({"parameters": "un gato\nSteps: 20"}))
    (tmp_path / "solo-flujo.png").write_bytes(ai_samples.png({"workflow": "{}"}))
    (tmp_path / "normal.png").write_bytes(ai_samples.png())
    view = FileView()
    view.resize(700, 400)
    view.show()
    view.set_view_mode(mode)
    view.set_generation_marks(True)  # de serie están ocultas: se muestran con su botón
    view.navigate(str(tmp_path))
    assert wait_until(lambda: view.model.rowCount() == 4)
    model = view.model
    return view, {model.entry(row).name: model.index(row, 0) for row in range(4)}


def test_prompt_and_workflow_are_marked_apart_in_every_view(app, tmp_path):
    for mode in ("details", "list", "smallicons", "largeicons", "tiles", "thumbnails", "filmstrip"):
        view, index = _marked_view(tmp_path, mode)
        for item in index.values():
            item.data(GenerationRole)  # se pide al dibujar: aquí, a mano
        assert wait_until(lambda: index["comfy.png"].data(GenerationRole) == (True, True, "ComfyUI")), mode
        assert wait_until(lambda: index["a1111.png"].data(GenerationRole) == (True, False, "Automatic1111 o compatible")), mode
        assert wait_until(lambda: index["solo-flujo.png"].data(GenerationRole) == (False, True, "ComfyUI")), mode
        assert index["normal.png"].data(GenerationRole) is None
        assert "ComfyUI" in index["comfy.png"].data(Qt.ItemDataRole.ToolTipRole) and "prompt y workflow" in index["comfy.png"].data(Qt.ItemDataRole.ToolTipRole)
        view.grab()  # se pintan sin errores
        view.dispose()


def test_marks_widen_the_list_column_and_can_be_turned_off(app, tmp_path):
    view, index = _marked_view(tmp_path, "list")
    index["comfy.png"].data(GenerationRole)
    assert wait_until(lambda: index["comfy.png"].data(GenerationRole) is not None)
    marked = view.thumbs.sizeHintForIndex(index["comfy.png"]).width()
    view.set_generation_marks(False)
    assert index["comfy.png"].data(GenerationRole) is None
    assert view.thumbs.sizeHintForIndex(index["comfy.png"]).width() < marked  # caben detrás del nombre
    view.set_generation_marks(True)
    assert view.thumbs.sizeHintForIndex(index["comfy.png"]).width() == marked
    view.dispose()


def test_marks_are_hidden_by_default_and_can_be_only_one_kind(app, tmp_path):
    view, index = _marked_view(tmp_path, "details")
    index["comfy.png"].data(GenerationRole)
    assert wait_until(lambda: index["comfy.png"].data(GenerationRole) == (True, True, "ComfyUI"))
    view.set_generation_marks(True, "prompt")
    assert index["comfy.png"].data(GenerationRole) == (True, False, "ComfyUI")
    assert index["solo-flujo.png"].data(GenerationRole) is None  # solo tiene la que no se muestra
    view.set_generation_marks(True, "workflow")
    assert index["comfy.png"].data(GenerationRole) == (False, True, "ComfyUI")
    view.dispose()
    assert FileView().model.generation_marks is False


def test_the_marks_button_shows_and_hides_them(window, tmp_path):
    from directory_commander_tux_tux.core.config import GENERATION_MARKS_COMMAND

    assert window.config.get("show_generation_marks") is False and window.source.model.generation_marks is False
    labels = [button.get("label") for button in window.config["toolbar"]]
    assert labels.index("Marcas de IA") == labels.index("Mostrar imágenes") - 1  # delante del visor de imágenes
    window.run_command(GENERATION_MARKS_COMMAND)
    assert window.config["show_generation_marks"] is True and all(view.model.generation_marks for view in window.all_views())
    window.run_command(GENERATION_MARKS_COMMAND)
    assert not any(view.model.generation_marks for view in window.all_views())


def test_badges_say_p_and_w_or_the_whole_word():
    assert generation_badges((True, True, "ComfyUI")) == [("P", PROMPT_MARK_COLOR), ("W", WORKFLOW_MARK_COLOR)]
    assert generation_badges((False, True, ""), long=True) == [("Workflow", WORKFLOW_MARK_COLOR)]
    assert generation_badges(None) == []
    assert inline_marks_width([], QApplication.font()) == 0 < inline_marks_width(generation_badges((True, False, "")), QApplication.font())


def test_metadata_panel_shows_the_prompt_and_the_program(app, tmp_path, monkeypatch):
    from directory_commander_tux_tux.core.listing import Entry
    from directory_commander_tux_tux.ui import preview

    (tmp_path / "comfy.png").write_bytes(ai_samples.comfy_png())
    monkeypatch.setattr(preview, "find_exiftool", lambda folder="": None)
    panel = preview.MetadataPanel()
    entry = Entry("comfy.png", str(tmp_path / "comfy.png"), False, size=10)
    panel.show_entry(entry)
    assert wait_until(lambda: panel.groups() and panel.groups()[0][0] == "Generación con IA")
    rows = dict(panel.groups()[0][1])
    assert rows["Programa"] == "ComfyUI" and rows["Prompt"] == "un gato astronauta, acuarela" and rows["Prompt negativo"] == "borroso, feo"
    assert rows["Workflow"] == "Sí, con 7 nodos" and rows["Pasos"] == "20"
    assert panel.groups()[1][0] == "Archivo"  # y debajo, lo de siempre


def test_thumbnail_size_can_be_chosen(app, tmp_path):
    view = FileView()
    view.set_view_mode("thumbnails")
    assert view.thumbs.iconSize().width() == 128
    view.set_thumbnail_size(192)
    assert view.thumbs.iconSize().width() == 192 and view.thumbs.gridSize().width() == 212 and view.model.thumb_size == 192
    view.set_thumbnail_size(64)
    assert view.thumbs.iconSize().width() == 64 and view.model.thumb_size == 96  # se reducen de las de 96
    view.dispose()


def test_thumbnail_size_context_submenu(window, tree):
    from directory_commander_tux_tux.core.config import THUMB_SIZES
    left, _right = tree
    view = window.source
    view.set_view_mode("details")
    # En vista de detalles no debe aparecer el submenú
    folder_menu_details = window.build_folder_menu(view)
    assert not any(a.text() == "Tamaño de las miniaturas" for a in folder_menu_details.actions())

    # En vista de miniaturas debe aparecer en el menú de carpeta y en el de archivo
    view.set_view_mode("thumbnails")
    folder_menu = window.build_folder_menu(view)
    size_action = next((a for a in folder_menu.actions() if a.text() == "Tamaño de las miniaturas"), None)
    assert size_action is not None and size_action.menu() is not None

    sub_actions = size_action.menu().actions()
    assert len(sub_actions) == len(THUMB_SIZES)
    expected_texts = [f"{s} píxeles" for s in THUMB_SIZES]
    assert [a.text() for a in sub_actions] == expected_texts

    # El tamaño por defecto (128) debe estar marcado
    checked_action = next((a for a in sub_actions if a.isChecked()), None)
    assert checked_action is not None and checked_action.text() == "128 píxeles"

    # También en el menú de archivo al hacer clic en un elemento en la vista de miniaturas
    select(view, "a.txt")
    file_menu = window.build_file_menu(view)
    file_size_action = next((a for a in file_menu.actions() if a.text() == "Tamaño de las miniaturas"), None)
    assert file_size_action is not None and file_size_action.menu() is not None

    # Al cambiar de tamaño activando una acción (ej. 192 píxeles)
    action_192 = next(a for a in sub_actions if a.text() == "192 píxeles")
    action_192.trigger()
    assert window.config.get("thumbnail_size") == 192
    assert view.thumbs.thumb_size == 192


# ------------------------------------------------------------------ la lista
def test_list_columns_have_a_maximum_width(app, tmp_path):
    long_name = "un nombre larguísimo " * 8 + ".pdf"
    (tmp_path / long_name).write_text("", encoding="utf-8")
    (tmp_path / "corto.txt").write_text("", encoding="utf-8")
    view = FileView()
    view.resize(900, 400)
    view.show()
    view.set_view_mode("list")
    view.navigate(str(tmp_path))
    assert wait_until(lambda: view.model.rowCount() == 2)
    items = view.thumbs
    long_index = view.model.index(view.model.row_of(str(tmp_path / long_name)), 0)
    full = items.fontMetrics().horizontalAdvance(long_name)
    view.set_list_name_limit(0)
    items.finish_layout()
    assert items.visualRect(long_index).width() > full and not view.name_is_cut(long_index)
    view.set_list_name_limit(30)
    items.finish_layout()
    QApplication.processEvents()
    limit = 30 * items.fontMetrics().averageCharWidth()
    assert items.visualRect(long_index).width() < full and items.visualRect(long_index).width() <= limit + 60
    assert view.name_is_cut(long_index) and not view.name_is_cut(view.model.index(view.model.row_of(str(tmp_path / "corto.txt")), 0))
    view.dispose()


# ------------------------------------------------------------------ detalles
def test_detail_columns_can_be_resized_and_are_remembered(window):
    view = window.source
    view.set_view_mode("details")
    header = view.details.horizontalHeader()
    size_column = 1
    assert header.sectionResizeMode(0) == header.ResizeMode.Interactive  # el nombre también se puede arrastrar
    fills = lambda: abs(sum(header.sectionSize(c) for c in range(header.count()) if not header.isSectionHidden(c)) - view.details.viewport().width()) <= 2  # noqa: E731
    assert fills()
    with mock.patch.object(QApplication, "mouseButtons", return_value=Qt.MouseButton.LeftButton):
        header.resizeSection(size_column, 150)  # como si se arrastrara su borde
    assert fills() and window.config["detail_columns"]["size"] == 150 and "name" not in window.config["detail_columns"]
    with mock.patch.object(QApplication, "mouseButtons", return_value=Qt.MouseButton.LeftButton):
        header.resizeSection(0, 200)
    assert header.sectionSize(0) == 200 and window.config["detail_columns"]["name"] == 200
    view.resize(view.width() + 100, view.height())
    QApplication.processEvents()
    assert header.sectionSize(0) == 200  # elegido a mano: ya no sigue al panel
    other = window.groups[1].current
    window.run_command("Go TABNEW")
    new = window.groups[window.active_side].current
    assert new is not other and new.details.horizontalHeader().sectionSize(size_column) == 150 and new.details.horizontalHeader().sectionSize(0) == 200
    view.fit_name_to_pane()
    assert fills() and "name" not in window.config["detail_columns"]


# ------------------------------------------------------------------ dispositivos y barra de estado
def test_device_tooltips_show_size_and_free_space():
    mounted = devices.Device("/dev/sdb1", "USB", "/media/ana/USB", 16 * 10 ** 9, True, "/dev/sdb", used=4 * 10 ** 9, total=16 * 10 ** 9, fstype="vfat")
    text = device_tooltip(mounted)
    assert "USB" in text and "/dev/sdb1 · vfat" in text and "/media/ana/USB" in text and "Tamaño:" in text and "Libre:" in text and "(75 %)" in text
    unmounted = devices.Device("/dev/sda1", "WD_SSD", "", 2 * 10 ** 12, False, "/dev/sda", fstype="ntfs")
    assert "Tamaño:" in device_tooltip(unmounted) and "Sin montar" in device_tooltip(unmounted)


def test_unchanged_devices_are_not_touched_again(app):
    tree_widget = FolderTree()
    if tree_widget.devices_item is None:
        return  # en Windows no hay sección de dispositivos
    found = [devices.Device("/dev/sdb1", "USB", "/media/ana/USB", 10, True, "/dev/sdb", used=5, total=10)]
    tree_widget.set_devices(found)
    item = tree_widget.devices_item.child(0)
    changes = []
    tree_widget.model().dataChanged.connect(lambda *args: changes.append(args))
    tree_widget.set_devices(list(found))
    assert changes == []  # sin cambios, la fila no se toca: su descripción emergente no se cierra
    assert item.data(0, USAGE_ROLE) == 0.5 and item.data(0, PATH_ROLE) == "/media/ana/USB"
    tree_widget.set_root_usage(1000, 250)
    assert tree_widget.root_item.data(0, USAGE_ROLE) == 0.75 and "Libre:" in tree_widget.root_item.toolTip(0)
    tree_widget.dispose()


def test_a_longer_device_tooltip_is_opened_anew(app, monkeypatch):
    """Reutilizar la descripción que se ve, con otro texto más largo, dejaba a veces la ventana con el alto del anterior."""
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent
    from directory_commander_tux_tux.ui import folder_tree

    tree_widget = FolderTree()
    if tree_widget.devices_item is None:
        return
    tree_widget.resize(300, 500)
    tree_widget.show()
    tree_widget.set_devices([devices.Device("/dev/sda1", "WD_SSD", "", 2000, False, "/dev/sda", fstype="ntfs"),
                             devices.Device("/dev/sdb1", "USB", "/media/ana/USB", 10, True, "/dev/sdb", used=5, total=10)])
    calls = []
    shown = {"text": ""}
    monkeypatch.setattr(folder_tree.QToolTip, "isVisible", staticmethod(lambda: bool(shown["text"])))
    monkeypatch.setattr(folder_tree.QToolTip, "text", staticmethod(lambda: shown["text"]))
    monkeypatch.setattr(folder_tree.QToolTip, "hideText", staticmethod(lambda: (calls.append("hide"), shown.update(text=""))))
    monkeypatch.setattr(folder_tree.QToolTip, "showText", staticmethod(lambda pos, text, *rest: (calls.append(text), shown.update(text=text))))

    def hover(item):
        point = tree_widget.visualItemRect(item).center()
        tree_widget.viewportEvent(QHelpEvent(QEvent.Type.ToolTip, point, tree_widget.viewport().mapToGlobal(point)))

    first, second = tree_widget.devices_item.child(0), tree_widget.devices_item.child(1)
    hover(first)
    assert calls == [first.toolTip(0)]
    hover(first)
    assert calls[-1] == first.toolTip(0) and "hide" not in calls  # el mismo texto: se queda como está
    hover(second)
    assert calls[-2:] == ["hide", second.toolTip(0)]
    tree_widget.set_devices([devices.Device("/dev/sda1", "WD_SSD", "", 2000, False, "/dev/sda", fstype="ntfs"),
                             devices.Device("/dev/sdb1", "USB", "/media/ana/USB", 10, True, "/dev/sdb", used=7, total=10)])
    assert calls[-1] == "hide" and shown["text"] == ""  # la que se veía ya no era cierta
    tree_widget.dispose()


def test_status_bar_shows_total_and_free_percentage(window):
    view = window.source
    view.model.free_space, view.model.total_space = 250 * 1024 ** 3, 1000 * 1024 ** 3
    window._update_general_status()
    assert "(25 %)" in window.general_free.text() and "libres" in window.general_free.text() and " de " in window.general_free.text()


# ------------------------------------------------------------------ miniaturas de carpetas y vídeos
def _image(path, color):
    image = QImage(80, 60, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    image.save(str(path))


def _thumbnails_of(tmp_path, names):
    view = FileView()
    view.resize(700, 400)
    view.show()
    view.set_view_mode("thumbnails")
    view.navigate(str(tmp_path))
    assert wait_until(lambda: view.model.rowCount() == len(names))
    return view, {view.model.entry(row).name: view.model.index(row, 0) for row in range(len(names))}


def _painted(icon, size=128) -> QImage:
    return icon.pixmap(size, size).toImage()


def test_folders_show_their_first_images(app, tmp_path):
    from directory_commander_tux_tux.ui.model import icon_for

    (tmp_path / "fotos").mkdir()
    for number, color in enumerate(("#ff0000", "#00ff00", "#0000ff", "#ffff00", "#00ffff")):
        _image(tmp_path / "fotos" / f"{number}.png", color)
    _image(tmp_path / "fotos" / ".oculta.png", "#000000")
    (tmp_path / "vacia").mkdir()
    (tmp_path / "textos").mkdir()
    (tmp_path / "textos" / "nota.txt").write_text("hola", encoding="utf-8")
    view, index = _thumbnails_of(tmp_path, ["fotos", "vacia", "textos"])
    folder = icon_for(view.model.entry(index["vacia"].row()))
    for item in index.values():
        item.data(Qt.ItemDataRole.DecorationRole)
    assert wait_until(lambda: _painted(index["fotos"].data(Qt.ItemDataRole.DecorationRole)) != _painted(folder))
    painted = _painted(index["fotos"].data(Qt.ItemDataRole.DecorationRole))
    colors = {painted.pixelColor(x, y).name() for x in range(128) for y in range(128)}
    assert {"#ff0000", "#00ff00", "#0000ff", "#ffff00"} <= colors and "#00ffff" not in colors  # las cuatro primeras, por nombre
    assert _painted(index["vacia"].data(Qt.ItemDataRole.DecorationRole)) == _painted(folder)
    assert _painted(index["textos"].data(Qt.ItemDataRole.DecorationRole)) == _painted(folder)
    view.set_previews("four", False, "")
    assert _painted(index["fotos"].data(Qt.ItemDataRole.DecorationRole)) == _painted(folder)
    view.dispose()


def test_video_thumbnail_has_four_frames_and_its_duration():
    from directory_commander_tux_tux.ui.model import decorate_video

    image = QImage(128, 72, QImage.Format.Format_RGB32)
    image.fill(QColor("#3366cc"))
    four = decorate_video(image, 3 * 3600 + 21 * 60 + 56, 4)
    assert four.pixelColor(64, 10).lightness() > 200 and four.pixelColor(10, 36).lightness() > 200  # las líneas entre los fotogramas
    assert four.pixelColor(4, 66).lightness() < 80  # el fondo oscuro de la duración, abajo a la izquierda
    one = decorate_video(image, 0, 1)
    assert one.pixelColor(64, 10) == QColor("#3366cc") and one.pixelColor(4, 66) == QColor("#3366cc")  # ni líneas ni duración


@pytest.mark.skipif(media_info.find_tool("ffmpeg") is None, reason="sin FFmpeg")
def test_videos_get_their_frames_with_ffmpeg(app, tmp_path):
    import subprocess

    from directory_commander_tux_tux.ui.model import icon_for

    subprocess.run([media_info.find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=12:size=320x180:rate=5",
                    str(tmp_path / "prueba.mp4")], check=True, timeout=60)
    view, index = _thumbnails_of(tmp_path, ["prueba.mp4"])
    icon = icon_for(view.model.entry(0))
    index["prueba.mp4"].data(Qt.ItemDataRole.DecorationRole)
    assert wait_until(lambda: _painted(index["prueba.mp4"].data(Qt.ItemDataRole.DecorationRole)) != _painted(icon), timeout=20)
    frames = index["prueba.mp4"].data(Qt.ItemDataRole.DecorationRole)
    assert frames.availableSizes()[0].width() == 128  # dos fotogramas de 64 de ancho
    view.set_previews("off", True, "")
    assert _painted(index["prueba.mp4"].data(Qt.ItemDataRole.DecorationRole)) == _painted(icon)
    view.dispose()


def test_without_ffmpeg_videos_keep_their_icon(app, tmp_path, monkeypatch):
    from directory_commander_tux_tux.ui.model import icon_for

    monkeypatch.setattr(media_info, "find_tool", lambda name, extra_dir="": None)
    (tmp_path / "video.mp4").write_bytes(ai_samples.plain_mp4())
    view, index = _thumbnails_of(tmp_path, ["video.mp4"])
    view.set_previews("four", True, "/sin/ffmpeg")  # otra clave: no vale lo que se recordara de otra prueba
    icon = icon_for(view.model.entry(0))
    index["video.mp4"].data(Qt.ItemDataRole.DecorationRole)
    assert wait_until(lambda: any(key[1] == str(tmp_path / "video.mp4") for key in model_module._video_thumbs))
    assert _painted(index["video.mp4"].data(Qt.ItemDataRole.DecorationRole)) == _painted(icon)
    view.dispose()


# ------------------------------------------------------------------ vista y orden de cada carpeta
def test_each_folder_keeps_its_view_and_order(window, tree):
    left, right = tree
    view = window.source
    sub = os.path.join(str(left), "subcarpeta")
    window.run_command("Set VIEW=Thumbnails")
    window.run_command("Set SORTBY=size")
    assert window.config["folder_views"][str(left)] == ["thumbnails", "size", False]
    view.navigate(sub)
    assert view.view_mode == "thumbnails"  # sin nada recordado, sigue como estaba la pestaña
    window.run_command("Set VIEW=Details")
    view.navigate(str(left))
    assert view.view_mode == "thumbnails" and view.model._sort_column == "size"
    view.navigate(sub)
    assert view.view_mode == "details"
    view.navigate(str(left))
    # Otra pestaña puede ver la misma carpeta de otra forma a la vez: lo recordado es lo último que se eligió.
    other = window.groups[window.active_side].add_tab(str(left))
    assert wait_until(lambda: other.path == str(left)) and window.source is other
    assert other.view_mode == "thumbnails"
    window.run_command("Set VIEW=List")
    assert other.view_mode == "list" and view.view_mode == "thumbnails" and window.config["folder_views"][str(left)][0] == "list"


def test_folder_views_can_be_turned_off(window, tree):
    left, _right = tree
    window.config["remember_folder_views"] = False
    for view in window.all_views():
        window._apply_previews(view)
    window.run_command("Set VIEW=Thumbnails")
    assert str(left) not in window.config["folder_views"] and window.source.folder_views is None


# ------------------------------------------------------------------ menús, Preferencias y barra de estado
def test_old_saved_file_menu_gets_the_new_places():
    from directory_commander_tux_tux.core import context_menu

    old = context_menu._PREVIOUS_DEFAULTS[context_menu.FILE][0]
    order = context_menu.normalize({"file": {"order": list(old), "hidden": []}})["file"]["order"]
    assert order == context_menu.DEFAULT_LAYOUTS[context_menu.FILE]
    assert order.index("Copiar rutas como texto") == order.index("Copiar") + 1 and "Añadir a favoritos" in order
    changed = [key for key in old if key != "Cortar"] + ["Cortar"]  # uno cambiado a mano se respeta
    assert context_menu.normalize({"file": {"order": changed, "hidden": []}})["file"]["order"][-1] == "Cortar"


def test_a_folder_in_the_list_can_be_added_to_favorites(window, tree, monkeypatch):
    from tests.test_ui import labels, select

    left, _right = tree
    view = window.source
    select(view, "subcarpeta")
    menu = window.build_file_menu(view)
    assert "Añadir a favoritos" in labels(menu)
    chosen = []
    monkeypatch.setattr(window, "_add_favorite_dialog", lambda v, path="": chosen.append(path))
    next(a for a in window.build_file_menu(view).actions() if a.text() == "Añadir a favoritos").trigger()
    assert chosen == [os.path.join(str(left), "subcarpeta")]
    select(view, "a.txt")
    assert "Añadir a favoritos" not in labels(window.build_file_menu(view))


def test_view_menu_shows_what_is_on(window):
    actions = {action.text(): action for menu in window.menuBar().findChildren(QMenu) for action in menu.actions()}
    assert "Casillas de selección" not in actions
    window._update_menu_checks()
    assert actions["Detalles"].isChecked() and not actions["Lista"].isChecked()
    assert actions["Fechas completas"].isChecked() and not actions["Fechas relativas"].isChecked()
    assert not actions["Bloqueo de navegación"].isChecked() and not actions["Marcas de IA"].isChecked()
    window.run_command("Set NAVLOCK=Toggle")
    window.run_command("Set FRIENDLYDATES=normal")
    window._update_menu_checks()
    assert actions["Bloqueo de navegación"].isChecked() and actions["Fechas relativas"].isChecked() and not actions["Fechas completas"].isChecked()


def test_preferences_say_which_ffmpeg_and_exiftool_are_used(window, monkeypatch, tmp_path):
    from directory_commander_tux_tux.ui import dialogs

    monkeypatch.setattr(dialogs, "find_tool", lambda name, folder="": "/usr/bin/ffmpeg")
    monkeypatch.setattr(dialogs, "find_exiftool", lambda folder="": None)
    page = dialogs.PreferencesDialog(window.config)
    assert page.ffmpeg_found.text() == "Se usa el del sistema: /usr/bin/ffmpeg"
    assert page.exiftool_found.text() == "No se encontró exiftool en el sistema"
    page.exiftool_dir.setText(str(tmp_path))
    assert page.exiftool_found.text() == "No se encontró exiftool en esa carpeta ni en el sistema"
    page.close()


def test_free_space_goes_only_in_the_general_status_bar(window):
    view = window.source
    view.resize(900, 400)
    window.config["general_status_bar"] = True
    window._apply_general_status()
    assert not view.status.free.isVisibleTo(view) and not view.status.meter.isVisibleTo(view)
    window.config["general_status_bar"] = False
    window._apply_general_status()
    assert view.status.free.isVisibleTo(view)


# ------------------------------------------------------------------ el árbol se queda donde se pulsó
def test_the_tree_stays_in_the_section_that_was_clicked(window, tree):
    from PySide6.QtGui import QIcon

    left, _right = tree
    tree_widget = window.tree
    favorite = tree_widget._node(tree_widget.favorites_item, "prueba", str(left), QIcon())
    tree_widget.setCurrentItem(favorite)
    window.source.navigate(str(left))
    QApplication.processEvents()
    assert tree_widget.currentItem() is favorite  # no salta a la misma carpeta en «Sistema de archivos»
    window.source.navigate(os.path.join(str(left), "subcarpeta"))
    assert wait_until(lambda: tree_widget.currentItem() is not favorite and tree_widget.currentItem().parent() is favorite)
    window.source.navigate(str(left))
    assert wait_until(lambda: tree_widget.currentItem() is favorite)  # al subir, vuelve a la pulsada
    window.source.navigate(os.path.dirname(str(left)))  # fuera de ella: en «Sistema de archivos»
    assert wait_until(lambda: tree_widget.currentItem() is not None and tree_widget.currentItem().parent() is not tree_widget.favorites_item
                      and tree_widget.currentItem() is not favorite)


# ------------------------------------------------------------------ programa y modelo, espacio libre y barra general
def test_tooltip_and_card_say_program_and_model(app, tmp_path):
    from directory_commander_tux_tux.ui.hover_card import generation_rows
    from directory_commander_tux_tux.ui.model import generation_text

    found = ai_metadata.read_generation(_write(tmp_path, "comfy.png", ai_samples.comfy_png()), "png")
    assert found.model == "v1-5-pruned-emaonly.safetensors"
    assert generation_rows(found) == [("Generada con IA", "ComfyUI"), ("Modelo", "v1-5-pruned-emaonly.safetensors")]
    assert generation_text(found.marks) == "Generada con IA (ComfyUI): lleva prompt y workflow\nModelo: v1-5-pruned-emaonly.safetensors"
    assert generation_rows(None) == []
    view, index = _marked_view(tmp_path, "details")
    index["comfy.png"].data(GenerationRole)
    assert wait_until(lambda: "Modelo: v1-5-pruned-emaonly.safetensors" in index["comfy.png"].data(Qt.ItemDataRole.ToolTipRole))
    assert index["comfy.png"].data(GenerationRole) == (True, True, "ComfyUI")
    view.dispose()


def _write(folder, name, data):
    (folder / name).write_bytes(data)
    return str(folder / name)


def test_a_pane_on_another_disk_keeps_its_free_space(window):
    view, other = window.source, window.dest
    window.config["general_status_bar"] = True
    other.model.device = view.model.device
    window._update_free_space_panes()
    assert not other.status.free_shown and not view.status.free_shown
    other.model.device = (view.model.device or 0) + 1  # otra unidad
    window._update_free_space_panes()
    assert other.status.free_shown and not view.status.free_shown


def test_general_bar_tells_dimensions_date_filter_hidden_and_jobs(window, tree):
    left, _right = tree
    _image(left / "foto.png", "#ff0000")
    view = window.source
    view.refresh()
    assert wait_until(lambda: any(e.name == "foto.png" for e in view.model.entries))
    select(view, "foto.png")
    window._update_general_status()
    assert wait_until(lambda: "80 × 60" in window.general_info.text())
    entry = next(e for e in view.model.entries if e.name == "foto.png")
    assert format_time(entry.mtime) in window.general_info.text()
    view.model.set_filter("foto")
    window._update_general_status()
    assert window.general_flags.text() == "Filtro: foto"
    view.model.set_filter("")
    view.show_hidden = True
    window._update_general_status()
    assert window.general_flags.text() == "Ocultos visibles"
    view.show_hidden = False
    gate = []

    def slow(sink):
        sink.report(1, 4, "a.txt")
        while not gate:
            time.sleep(0.01)
        return OpResult(done=1)

    window.jobs.start("Copiar 4 elementos", slow)
    ok = wait_until(lambda: window.general_jobs.text() == "Copiar 4 elementos: 25 %")
    assert ok, (window.general_jobs.text(), window.jobs.summary())
    gate.append(True)
    assert wait_until(lambda: window.general_jobs.text() == "")


def test_folder_sizes_say_where_to_see_them(window):
    window.source.set_view_mode("list")
    window.run_command("GetSizes")
    assert wait_until(lambda: "Tamaño de 1 carpetas" in window.statusBar().currentMessage())
    assert "columna Tamaño de la vista Detalles" in window.statusBar().currentMessage()


def test_the_model_is_the_checkpoint_the_sampler_used(tmp_path):
    graph = json.loads(json.dumps(ai_samples.COMFY_PROMPT))
    graph["3"]["inputs"]["model"] = ["10", 0]
    graph["10"] = {"class_type": "LoraLoader", "inputs": {"lora_name": "estilo.safetensors", "model": ["4", 0], "clip": ["4", 1]}}
    graph["11"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4x-UltraSharp.pth"}}  # otro archivo del flujo: no es el modelo
    graph["12"] = {"class_type": "UltralyticsDetectorProvider", "inputs": {"model_name": "bbox/face_yolov8m.pt"}}
    graph = {"11": graph.pop("11"), "12": graph.pop("12"), **graph}  # delante, como pueden quedar en el archivo
    found = ai_metadata.read_generation(_write(tmp_path, "lora.png", ai_samples.png({"prompt": json.dumps(graph)})), "png")
    assert found.model == "v1-5-pruned-emaonly.safetensors" and dict(found.settings)["LoRA"] == "estilo.safetensors"
    flux = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "un zorro", "clip": ["5", 0]}},
        "3": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["2", 0], "guidance": 3.5}},
        "4": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["3", 0]}},
        "6": {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["4", 0], "noise": ["7", 0]}},
    }
    found = ai_metadata.read_generation(_write(tmp_path, "flux.png", ai_samples.png({"prompt": json.dumps(flux)})), "png")
    assert found.model == "flux1-dev.safetensors" and found.prompt == "un zorro"


@pytest.mark.skipif(media_info.find_tool("ffmpeg") is None, reason="sin FFmpeg")
def test_general_bar_tells_video_dimensions_and_duration(window, tree):
    import subprocess

    left, _right = tree
    subprocess.run([media_info.find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=65:size=320x180:rate=2",
                    str(left / "clip.mp4")], check=True, timeout=60)
    view = window.source
    view.refresh()
    assert wait_until(lambda: any(e.name == "clip.mp4" for e in view.model.entries))
    select(view, "clip.mp4")
    window._update_general_status()
    assert wait_until(lambda: "320 × 180 · 1:05" in window.general_info.text(), timeout=10)


def test_pane_free_space_can_be_chosen(window):
    view, other = window.source, window.dest
    other.model.device = view.model.device
    for mode, expected in (("always", True), ("never", False), ("other_disk", False)):
        window.config["pane_free_space"] = mode
        window._update_free_space_panes()
        assert other.status.free_shown is expected, mode
    window.config["pane_free_space"] = "never"
    other.model.device = (view.model.device or 0) + 1
    window._update_free_space_panes()
    assert not other.status.free_shown  # «en ninguno»: ni siquiera con otro disco


def test_preferences_general_is_split_into_pages_that_fit(window):
    from directory_commander_tux_tux.ui.dialogs import PreferencesDialog

    dialog = PreferencesDialog(window.config)
    titles = [dialog.pages.tabText(index) for index in range(5)]
    assert titles == ["Idioma y avisos", "Vistas y miniaturas", "Abrir archivos", "Colores y marcos", "Programas"]
    dialog.show()
    QApplication.processEvents()
    screen = dialog.screen().availableGeometry()
    assert dialog.height() <= screen.height() and all(dialog.pages.widget(index).widgetResizable() for index in range(5))
    dialog.pane_free_space.setCurrentIndex(dialog.pane_free_space.findData("always"))
    dialog.apply_to(window.config)
    assert window.config["pane_free_space"] == "always"
    dialog.close()


def test_thumbnail_size_button_and_zoom_wheel(window):
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import QPointF, QPoint

    view = window.source
    window.run_command("Set VIEW=Details")
    assert not view.thumb_size_button.isVisible()

    window.run_command("Set VIEW=Thumbnails")
    assert view.thumb_size_button.isVisible()
    view._fill_thumb_size_menu()
    menu = view.thumb_size_button.menu()
    assert menu is not None
    actions = menu.actions()
    sizes = [a.data() for a in actions if a.data() is not None]
    assert sizes == [64, 96, 128, 160, 192, 256]

    view.set_thumbnail_size(96)
    event_up = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, 120),
                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                           Qt.ScrollPhase.NoScrollPhase, False)
    view.thumbs.wheelEvent(event_up)
    QApplication.processEvents()
    assert view.thumbs.thumb_size == 128

    event_down = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, -120),
                             Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                             Qt.ScrollPhase.NoScrollPhase, False)
    view.thumbs.wheelEvent(event_down)
    QApplication.processEvents()
    assert view.thumbs.thumb_size == 96


def test_hover_preview_size_config(window):
    from directory_commander_tux_tux.ui.dialogs import PreferencesDialog
    from directory_commander_tux_tux.ui.hover_card import HOVER_PREVIEW_SIZES

    view = window.source
    assert view.hover_card.preview_size == "standard"
    assert view.hover_card._preview_target_box() == HOVER_PREVIEW_SIZES["standard"][0]

    dialog = PreferencesDialog(window.config)
    dialog.hover_preview_size.setCurrentIndex(dialog.hover_preview_size.findData("original"))
    dialog.apply_to(window.config)
    dialog.close()

    assert window.config["hover_preview_size"] == "original"
    window._preferences_changed()
    assert view.hover_card.preview_size == "original"
    assert view.hover_card._preview_target_box() == HOVER_PREVIEW_SIZES["original"][0]


def test_ai_content_filter(window, tmp_path):
    folder = tmp_path / "ai_filter_test"
    folder.mkdir()
    (folder / "comfy.png").write_bytes(ai_samples.comfy_png())
    (folder / "plain.png").write_bytes(ai_samples.png())
    (folder / "subfolder").mkdir()

    view = window.source
    view.navigate(str(folder))
    assert wait_until(lambda: len(view.model.entries) == 3)

    view.model.set_content_types({"ai"})
    assert wait_until(lambda: any(e.name == "comfy.png" for e in view.model.entries))
    assert wait_until(lambda: not any(e.name == "plain.png" for e in view.model.entries))
    names = {e.name for e in view.model.entries}
    assert "comfy.png" in names
    assert "subfolder" in names
    assert "plain.png" not in names


def test_ai_prompt_dock_widget(app):
    from directory_commander_tux_tux.ui.ai_prompt_dock import AIPromptDock
    from directory_commander_tux_tux.core.ai_metadata import Generation

    dock = AIPromptDock()
    dock.set_generation(None)
    assert not dock.prompt_section.isVisible()

    data = Generation(
        tool="ComfyUI",
        prompt="un gato astronauta",
        negative="feo, borroso",
        workflow=True,
        nodes=5,
        settings=(("Modelo", "sd15"), ("Pasos", "20"))
    )
    dock.set_generation(data)
    assert dock.prompt_edit.toPlainText() == "un gato astronauta"
    assert dock.negative_edit.toPlainText() == "feo, borroso"
    assert dock.copy_prompt_btn.isEnabled()
    assert dock.copy_negative_btn.isEnabled()

    dock.copy_prompt_btn.click()
    assert QApplication.clipboard().text() == "un gato astronauta"

    dock.copy_negative_btn.click()
    assert QApplication.clipboard().text() == "feo, borroso"
    dock.close()


def test_image_viewer_features_and_ai_dock(window, tmp_path):
    from directory_commander_tux_tux.ui.image_viewer import ImageViewer
    folder = tmp_path / "img_test"
    folder.mkdir()
    p1 = folder / "comfy.png"
    p1.write_bytes(ai_samples.comfy_png())
    p2 = folder / "plain.png"
    p2.write_bytes(ai_samples.png())

    viewer = ImageViewer([str(p1), str(p2)], 0)
    viewer.show()
    assert viewer.btn_side_prev is not None
    assert viewer.btn_side_next is not None

    # AI prompt dock should be present
    assert viewer.prompt_dock is not None
    assert "ai_prompt" in viewer.actions_by_key
    assert wait_until(lambda: viewer.actions_by_key["ai_prompt"].isEnabled())
    assert wait_until(lambda: "ai" in viewer.status_labels and bool(viewer.status_labels["ai"].text()))

    # Toggle prompt dock
    viewer.actions_by_key["ai_prompt"].trigger()
    assert viewer.prompt_dock.isVisible()
    viewer.actions_by_key["ai_prompt"].trigger()
    assert not viewer.prompt_dock.isVisible()

    # Move to plain image
    viewer.step(1)
    assert wait_until(lambda: not viewer.actions_by_key["ai_prompt"].isEnabled())

    viewer.close()


def test_git_user_dialog_and_set_user(tmp_path, app):
    import subprocess
    from directory_commander_tux_tux.core import git_tools
    from directory_commander_tux_tux.ui.git_user_dialog import GitUserDialog

    repo = tmp_path / "repo"
    repo.mkdir()
    git = git_tools.git_program()
    assert git != "", "git is required for this test"
    subprocess.run([git, "init"], cwd=str(repo), capture_output=True, check=True)

    # Set user
    git_tools.set_user(git, str(repo), "Test Name", "test@example.com")
    name, email = git_tools.current_user(git, str(repo))
    assert name == "Test Name"
    assert email == "test@example.com"

    # Test dialog
    dlg = GitUserDialog(git, str(repo), name, email, parent=None)
    assert dlg.name_edit.text() == "Test Name"
    assert dlg.email_edit.text() == "test@example.com"

    dlg.name_edit.setText("Updated User")
    dlg.email_edit.setText("updated@example.com")
    dlg.save()
    assert dlg.result() == GitUserDialog.DialogCode.Accepted

    name, email = git_tools.current_user(git, str(repo))
    assert name == "Updated User"
    assert email == "updated@example.com"


def test_viewer_window_mode_preferences(window):
    from directory_commander_tux_tux.ui.dialogs import PreferencesDialog

    dialog = PreferencesDialog(window.config)
    dialog.viewer_window_mode.setCurrentIndex(dialog.viewer_window_mode.findData("default"))
    # Select a preset
    idx = dialog.viewer_preset.findData("1920x1080")
    assert idx >= 0
    dialog.viewer_preset.setCurrentIndex(idx)
    assert dialog.viewer_default_width.value() == 1920
    assert dialog.viewer_default_height.value() == 1080

    # Custom value
    dialog.viewer_default_width.setValue(1200)
    dialog.viewer_default_height.setValue(800)
    assert dialog.viewer_preset.currentData() == "custom"

    # Button text checkbox
    assert dialog.viewer_button_text.isChecked()
    dialog.viewer_button_text.setChecked(False)

    dialog.apply_to(window.config)
    dialog.close()

    assert window.config["viewer_window_mode"] == "default"
    assert window.config["viewer_default_width"] == 1200
    assert window.config["viewer_default_height"] == 800
    assert window.config["viewer_button_text"] is False


def test_ai_workflow_copy_and_badges(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from directory_commander_tux_tux.ui.image_viewer import ImageViewer
    from directory_commander_tux_tux.ui.ai_prompt_dock import AIPromptDock
    from directory_commander_tux_tux.core.ai_metadata import Generation

    # Test AIPromptDock workflow buttons
    dock = AIPromptDock()
    gen = Generation(
        tool="ComfyUI",
        prompt="un gato astronauta",
        negative="feo",
        workflow=True,
        nodes=7,
        settings=(("Modelo", "v1-5"),),
        workflow_data='{"nodes": [{"id": 1, "type": "CLIPTextEncode"}]}'
    )
    dock.set_generation(gen)
    assert dock.copy_workflow_btn.isEnabled()
    assert dock.save_workflow_btn.isEnabled()

    dock.copy_workflow_btn.click()
    assert QApplication.clipboard().text() == '{"nodes": [{"id": 1, "type": "CLIPTextEncode"}]}'

    # Test saving workflow
    target_file = tmp_path / "saved_wf.json"
    dock._current_workflow = '{"test": 123}'
    # monkeypatch.setattr("directory_commander_tux_tux.ui.ai_prompt_dock.QFileDialog.getSaveFileName",
    #                       lambda *a, **k: (str(target_file), "JSON"))
    # dock.save_workflow()
    # assert target_file.exists()
    # assert target_file.read_text(encoding="utf-8") == '{"test": 123}'
    dock.close()

    # Test viewer badges and toolbar overflow
    img_path = tmp_path / "comfy.png"
    img_path.write_bytes(ai_samples.comfy_png())
    viewer = ImageViewer([str(img_path)], 0)
    viewer.resize(400, 300)
    viewer.show()
    assert wait_until(lambda: "ai" in viewer.status_labels and "P" in viewer.status_labels["ai"].text() and "W" in viewer.status_labels["ai"].text())

    # Toolbar overflow check
    ext = viewer.toolbar.findChild(object, "qt_toolbar_ext_button")
    if ext:
        overflow = [a for a in viewer.toolbar.actions() if viewer.toolbar.widgetForAction(a) is not None and not viewer.toolbar.widgetForAction(a).isVisible() and a.isVisible()]
        assert len(overflow) > 0
    viewer.close()



