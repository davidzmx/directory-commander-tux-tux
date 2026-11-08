"""Visor de imágenes en ventana propia: navegación, zoom, giro, barra de información y operaciones sobre el archivo."""
from __future__ import annotations

import math
import os
import time

from PySide6.QtCore import QByteArray, QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QImageReader, QPixmap, QTransform
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QScrollArea,
                               QSizePolicy, QSlider, QToolBar, QToolButton, QWidgetAction)

from .. import APP_NAME
from ..core import ai_metadata, bar_buttons
from ..core.listing import format_size
from . import image_ops, theme
from .ai_prompt_dock import AIPromptDock
from .open_external import open_externally
from .background import run_bg
from .crop_overlay import CropOverlay
from .icons import app_icon, themed
from .shell_menu import tidy_separators
from ..core.i18n import tr

SLIDESHOW_MS = 3000
ZOOM_STEP = 1.25
ZOOM_MIN = 0.05
ZOOM_MAX = 16.0
SLIDER_STEPS = 1000
MAX_SIMPLE_ASPECT = 32


def format_aspect(width: int, height: int) -> str:
    """`4:3` si la proporción es sencilla y `1.33:1` si no."""
    if width <= 0 or height <= 0:
        return ""
    divisor = math.gcd(width, height)
    across, down = width // divisor, height // divisor
    if across <= MAX_SIMPLE_ASPECT and down <= MAX_SIMPLE_ASPECT:
        return f"{across}:{down}"
    return f"{width / height:.2f}:1"


def color_depth(image) -> int:
    """Bits por píxel de la imagen. Qt guarda un JPEG en 32 bits, pero solo tiene 24 de color."""
    depth = image.depth()
    if depth == 32 and not image.hasAlphaChannel():
        return 24
    return depth


def zoom_to_slider(zoom: float) -> int:
    return round(SLIDER_STEPS * math.log(zoom / ZOOM_MIN) / math.log(ZOOM_MAX / ZOOM_MIN))


def slider_to_zoom(value: int) -> float:
    return ZOOM_MIN * (ZOOM_MAX / ZOOM_MIN) ** (value / SLIDER_STEPS)


class ImageViewer(QMainWindow):
    """← → cambia de imagen, + − zoom, 0 ajustar, 1 tamaño real, F pantalla completa, S presentación, C recortar, Esc cerrar.

    `host` hace lo que afecta al archivo (eliminar, renombrar, mostrarlo en la lista). Sin él, esas acciones
    quedan desactivadas. `origin` es el panel en el que se marca la imagen que se está viendo."""

    def __init__(self, paths: list[str], start: int = 0, parent=None, host=None, origin=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowIcon(app_icon())
        self.host = host
        self.origin = origin
        self.config = getattr(host, "config", {}) if host is not None else {}
        self._apply_initial_geometry()
        self.paths = paths
        self.index = max(0, min(start, len(paths) - 1))
        self.zoom: float | None = None  # None = ajustar a la ventana
        self.fit_mode = "enlarge"  # "enlarge" llena la ventana; "shrink" solo reduce lo que no cabe
        self.rotation = 0
        self._pixmap: QPixmap | None = None
        self._shown: QPixmap | None = None  # la imagen con el giro aplicado
        self._generation = 0
        self._drag_from: QPoint | None = None
        self._was_maximized = False

        background = theme.current().panel
        self.label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet(f"background: {background}; color: {theme.current().text};")
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.label)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # las flechas cambian de imagen, no desplazan
        self.scroll.setStyleSheet(f"QScrollArea {{ background: {background}; border: none; }}")
        self.scroll.viewport().setMouseTracking(True)
        self.label.setMouseTracking(True)
        self.scroll.viewport().installEventFilter(self)
        self.label.installEventFilter(self)
        self.setCentralWidget(self.scroll)

        self.btn_side_prev = QToolButton(self.scroll.viewport())
        self.btn_side_next = QToolButton(self.scroll.viewport())
        for btn, icon, tip, delta in (
            (self.btn_side_prev, "goback", tr("Imagen anterior"), -1),
            (self.btn_side_next, "goforward", tr("Imagen siguiente"), 1),
        ):
            btn.setIcon(themed(icon))
            btn.setIconSize(QSize(28, 28))
            btn.setToolTip(tip)
            btn.setFixedSize(48, 64)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMouseTracking(True)
            btn.setStyleSheet("""
                QToolButton {
                    background-color: rgba(20, 20, 20, 0.7);
                    border: 1px solid rgba(255, 255, 255, 0.25);
                    border-radius: 8px;
                    padding: 4px;
                }
                QToolButton:hover {
                    background-color: rgba(0, 0, 0, 0.95);
                    border: 1px solid rgba(255, 255, 255, 0.7);
                }
            """)
            btn.clicked.connect(lambda _=False, d=delta: self.step(d))
            btn.installEventFilter(self)
            btn.hide()

        self.crop = CropOverlay(self.label)
        self.crop.hide()
        self.crop.selectionChanged.connect(self._crop_selection_changed)
        self.crop.confirmed.connect(self.save_crop)

        self.prompt_dock = AIPromptDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.prompt_dock)
        self.resizeDocks([self.prompt_dock], [420], Qt.Orientation.Horizontal)
        self.prompt_dock.hide()
        self.prompt_dock.visibilityChanged.connect(
            lambda v: self.actions_by_key["ai_prompt"].setChecked(v) if "ai_prompt" in self.actions_by_key else None
        )

        self.timer = QTimer(self)
        self.timer.setInterval(SLIDESHOW_MS)
        self.timer.timeout.connect(lambda: self.step(1))

        self.actions_by_key: dict[str, QAction] = {}
        self._icons: dict[str, str] = {}
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status_bar()
        self.load_current()

    def _apply_initial_geometry(self) -> None:
        mode = self.config.get("viewer_window_mode", "remember")
        if mode == "remember":
            geom = self.config.get("viewer_geometry", "")
            if geom:
                self.restoreGeometry(QByteArray.fromHex(geom.encode()))
                return
        w = int(self.config.get("viewer_default_width", 1120))
        h = int(self.config.get("viewer_default_height", 720))
        self.resize(w, h)

    # ---------------------------------------------------------------- interfaz
    def _action(self, key: str, label: str, icon: str, slot, shortcut: str = "", checkable: bool = False) -> None:
        action = QAction(themed(icon) if icon else themed(""), label, self)
        action.triggered.connect(lambda _checked=False: slot())
        if shortcut:
            action.setShortcut(shortcut)
            action.setToolTip(f"{label} ({shortcut})")
        action.setCheckable(checkable)
        self.addAction(action)  # así los atajos siguen activos en pantalla completa, con las barras ocultas
        self.actions_by_key[key] = action
        if icon:
            self._icons[key] = icon

    def _build_actions(self) -> None:
        add = self._action
        add("previous", tr("Imagen anterior"), "goback", lambda: self.step(-1), "Left")
        add("next", tr("Imagen siguiente"), "goforward", lambda: self.step(1), "Right")
        add("first", tr("Primera imagen"), "", lambda: self.go_to(0), "Home")
        add("last", tr("Última imagen"), "", lambda: self.go_to(len(self.paths) - 1), "End")
        add("rotate_left", tr("Girar a la izquierda"), "rotateleft", lambda: self.rotate(-90), "L")
        add("rotate_right", tr("Girar a la derecha"), "rotateright", lambda: self.rotate(90), "R")
        add("zoom_in", tr("Acercar"), "zoomin", lambda: self.change_zoom(ZOOM_STEP), "+")
        add("zoom_out", tr("Alejar"), "zoomout", lambda: self.change_zoom(1 / ZOOM_STEP), "-")
        add("actual", tr("Tamaño real"), "zoomactual", lambda: self.set_zoom(1.0), "1", True)
        add("fit_shrink", tr("Reducir para caber en la ventana"), "zoomshrink", lambda: self.set_fit("shrink"), "2", True)
        add("fit_enlarge", tr("Ampliar a la ventana"), "zoomfit", lambda: self.set_fit("enlarge"), "0", True)
        add("fullscreen", tr("Pantalla completa"), "fullscreen", self.toggle_fullscreen, "F11", True)
        add("slideshow", tr("Presentación"), "slideshow", self.toggle_slideshow, "S", True)
        add("crop", tr("Recortar"), "crop", lambda: self.set_cropping(not self.crop.isVisible()), "C", True)
        add("save_crop", tr("Guardar selección…"), "savecrop", self.save_crop, "Ctrl+S")
        add("copy", tr("Copiar la imagen"), "copy", self.copy_image, "Ctrl+C")
        add("ai_prompt", tr("Info de IA"), "comment", self.toggle_prompt_panel, checkable=True)
        add("rename", tr("Renombrar"), "rename", self.rename_current, "F2")
        add("delete", tr("Eliminar"), "delete", self.delete_current, "Del")
        add("reveal", tr("Mostrar en el panel"), "folder", self.reveal_current)
        add("open_system", tr("Abrir con la aplicación del sistema"), "", self.open_with_system)
        add("close", tr("Cerrar"), "", self.close)
        for key in ("rename", "delete", "reveal"):
            self.actions_by_key[key].setEnabled(self.host is not None)
        self.actions_by_key["save_crop"].setEnabled(False)
        self.actions_by_key["ai_prompt"].setEnabled(False)
        self.actions_by_key["ai_prompt"].setVisible(False)

    def _build_menus(self) -> None:
        by_key = self.actions_by_key
        bar = self.menuBar()
        for title, keys in ((tr("&Archivo"), ("open_system", "reveal", None, "close")),
                            (tr("&Editar"), ("copy", None, "crop", "save_crop", None, "rename", "delete")),
                            (tr("&Ver"), ("previous", "next", "first", "last", None, "zoom_in", "zoom_out", "actual", "fit_shrink",
                                      "fit_enlarge", None, "rotate_left", "rotate_right", None, "fullscreen", "slideshow", None, "ai_prompt"))):
            menu = bar.addMenu(title)
            for key in keys:
                menu.addSeparator() if key is None else menu.addAction(by_key[key])

    def _build_toolbar(self) -> None:
        self.toolbar = QToolBar(tr("Visor"))
        self.toolbar.setMovable(False)
        self.toolbar.installEventFilter(self)
        self.addToolBar(self.toolbar)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(0, SLIDER_STEPS)
        self.zoom_slider.setFixedWidth(120)
        self.zoom_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.zoom_slider.setToolTip("Zoom")
        self.zoom_slider.valueChanged.connect(lambda value: self.set_zoom(slider_to_zoom(value)))
        # Una acción propia: la que crea addWidget se destruye con el control al quitarla de la barra.
        self._zoom_action = QWidgetAction(self)
        self._zoom_action.setDefaultWidget(self.zoom_slider)
        self.apply_bar_buttons()

    def createPopupMenu(self) -> QMenu:
        """Clic derecho en la barra."""
        menu = QMenu(self)
        cfg = getattr(self.host, "config", self.config)
        show_text = cfg.get("viewer_button_text", True)
        text_act = menu.addAction(tr("Mostrar texto en los botones"))
        text_act.setCheckable(True)
        text_act.setChecked(show_text)
        text_act.toggled.connect(self._toggle_button_text)
        menu.addSeparator()
        customize = menu.addAction(themed("prefs"), tr("Personalizar…"), lambda: self.host.customize_bars(bar_buttons.IMAGE_VIEWER, self))
        customize.setEnabled(self.host is not None)
        return menu

    def _toggle_button_text(self, checked: bool) -> None:
        cfg = getattr(self.host, "config", self.config)
        cfg["viewer_button_text"] = checked
        self.apply_bar_buttons()

    def apply_bar_buttons(self) -> None:
        """Pone en la barra los botones elegidos en las preferencias. Lo que se quite sigue en los menús y con su atajo."""
        by_key = {**self.actions_by_key, "zoom_slider": self._zoom_action}
        config = getattr(self.host, "config", self.config)
        show_text = config.get("viewer_button_text", True)
        removed = bar_buttons.hidden(config, bar_buttons.IMAGE_VIEWER)
        self.toolbar.clear()
        for key in bar_buttons.arranged(config, bar_buttons.IMAGE_VIEWER):
            if key == bar_buttons.SEPARATOR:
                self.toolbar.addSeparator()
            elif key not in removed:
                self.toolbar.addAction(by_key[key])
                button = self.toolbar.widgetForAction(by_key[key])
                if isinstance(button, QToolButton):
                    if show_text and key in ("fullscreen", "crop", "save_crop", "rename", "delete", "reveal", "ai_prompt"):
                        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                    else:
                        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tidy_separators(self.toolbar)
        ext = self.toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
        if ext is not None:
            ext.installEventFilter(self)

    def _show_toolbar_overflow_menu(self, ext_btn: QWidget) -> None:
        menu = QMenu(self)
        for action in self.toolbar.actions():
            w = self.toolbar.widgetForAction(action)
            if w is not None and not w.isVisible() and action.isVisible():
                if action.isSeparator():
                    menu.addSeparator()
                else:
                    menu.addAction(action)
        if menu.actions():
            pt = ext_btn.mapToGlobal(QPoint(0, ext_btn.height()))
            menu.exec(pt)

    def _build_status_bar(self) -> None:
        self.status_labels: dict[str, QLabel] = {}
        bar = self.statusBar()
        bar.setSizeGripEnabled(True)
        bar.setStyleSheet("QStatusBar::item { border: none; }")  # los separadores ya son propios
        for position, key in enumerate(("name", "position", "format", "aspect", "mode", "size", "time", "selection", "ai")):
            if position:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.VLine)
                line.setStyleSheet(f"color: {theme.current().border};")
                bar.addWidget(line)
            label = QLabel()
            label.setContentsMargins(4, 0, 4, 0)
            label.setTextFormat(Qt.TextFormat.RichText if key == "ai" else Qt.TextFormat.PlainText)
            bar.addWidget(label)
            self.status_labels[key] = label
        self.status_labels["ai"].setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_labels["ai"].mousePressEvent = lambda _e: self.toggle_prompt_panel() if self.actions_by_key["ai_prompt"].isEnabled() else None

    def apply_icons(self) -> None:
        for key, icon in self._icons.items():
            self.actions_by_key[key].setIcon(themed(icon))

    # ---------------------------------------------------------------- carga
    @property
    def current_path(self) -> str:
        return self.paths[self.index] if self.paths else ""

    def load_current(self) -> None:
        self._generation += 1
        generation = self._generation
        path = self.paths[self.index]
        self.set_cropping(False)  # la selección era de la imagen anterior

        def read(_sink):
            started = time.monotonic()
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            kind = bytes(reader.format()).decode(errors="replace").upper()
            image = reader.read()
            return image, kind, time.monotonic() - started

        self.setWindowTitle(f"{path} — {APP_NAME}")
        self._show_file_status(path)
        if self.host is not None:
            self.host.viewer_image_changed(self.origin, path)
        run_bg(read, lambda result: self._loaded(generation, path, *result),
               lambda exc: self._failed(generation, path, str(exc)))

    def _show_file_status(self, path: str) -> None:
        labels = self.status_labels
        labels["name"].setText(os.path.basename(path))
        labels["position"].setText(f"{self.index + 1}/{len(self.paths)}")
        for key in ("format", "aspect", "mode", "time", "ai"):
            if key in labels:
                labels[key].setText("")
        try:
            labels["size"].setText(format_size(os.path.getsize(path)))
        except OSError:
            labels["size"].setText("")

    def _loaded(self, generation: int, path: str, image, kind: str, seconds: float) -> None:
        if generation != self._generation:
            return
        if image.isNull():
            self._failed(generation, path, tr("formato no admitido o archivo dañado"))
            return
        self._pixmap = self._shown = QPixmap.fromImage(image)
        self.rotation = 0  # el giro es solo para ver: no se guarda ni pasa a la imagen siguiente
        labels = self.status_labels
        labels["format"].setText(tr("{width} x {height} x {value} Imagen {kind}", width=image.width(), height=image.height(), value=color_depth(image), kind=kind).rstrip())
        labels["aspect"].setText(format_aspect(image.width(), image.height()))
        labels["time"].setText(tr("{seconds:.2f} seg", seconds=seconds))

        ext = os.path.splitext(path)[1].lstrip(".").lower()
        gen = ai_metadata.read_generation(path, ext)
        has_prompt = gen is not None and bool(gen.prompt)
        has_workflow = gen is not None and bool(gen.workflow)
        has_tool = gen is not None and bool(gen.tool)
        has_ai = gen is not None and (has_prompt or has_workflow or has_tool)
        self.actions_by_key["ai_prompt"].setVisible(has_ai)
        self.actions_by_key["ai_prompt"].setEnabled(has_ai)
        if "ai" in labels:
            if has_ai:
                badges = []
                if has_prompt:
                    badges.append('<span style="background-color: #8e44ad; color: #ffffff; font-weight: bold; border-radius: 3px; padding: 1px 4px; font-size: 11px;">P</span>')
                if has_workflow:
                    badges.append('<span style="background-color: #16a085; color: #ffffff; font-weight: bold; border-radius: 3px; padding: 1px 4px; font-size: 11px;">W</span>')
                if not badges and has_tool:
                    badges.append(f'<span style="background-color: #2980b9; color: #ffffff; font-weight: bold; border-radius: 3px; padding: 1px 4px; font-size: 11px;">{tr("IA")}</span>')
                labels["ai"].setText("&nbsp;".join(badges))
                tip = gen.prompt if gen.prompt else (gen.tool or tr("IA"))
                tip += f"\n{tr('Haz clic para ver/copiar la información de IA')}"
                labels["ai"].setToolTip(tip)
            else:
                labels["ai"].setText("")
        self.prompt_dock.set_generation(gen, path)
        self._apply_zoom()

    def _failed(self, generation: int, path: str, reason: str) -> None:
        if generation == self._generation:
            self._pixmap = self._shown = None
            self.label.setPixmap(QPixmap())
            self.label.setText(tr("No se pudo abrir {basename}: {reason}", basename=os.path.basename(path), reason=reason))
            if "ai" in self.status_labels:
                self.status_labels["ai"].setText("")
            self.actions_by_key["ai_prompt"].setVisible(False)
            self.actions_by_key["ai_prompt"].setEnabled(False)
            self.prompt_dock.set_generation(None)
            self._update_zoom_controls()

    # ---------------------------------------------------------------- zoom y giro
    def _apply_zoom(self) -> None:
        if self._shown is None:
            return
        self.label.setText("")
        source = self._shown
        if self.zoom is None:
            self.scroll.setWidgetResizable(True)
            target = self.scroll.viewport().size()
            fits = source.width() <= target.width() and source.height() <= target.height()
            if self.fit_mode == "shrink" and fits:
                scaled = source
            else:
                scaled = source.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        else:
            self.scroll.setWidgetResizable(False)
            scaled = source.scaled(source.size() * self.zoom, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            self.label.resize(scaled.size())
        self.label.setPixmap(scaled)
        self.crop.update()
        self._update_zoom_controls()

    def current_scale(self) -> float:
        if self.zoom is not None:
            return self.zoom
        shown = self.label.pixmap()
        if self._shown is None or shown is None or shown.isNull():
            return 1.0
        return shown.width() / max(1, self._shown.width())

    def _update_zoom_controls(self) -> None:
        by_key = self.actions_by_key
        by_key["actual"].setChecked(self.zoom == 1.0)
        by_key["fit_shrink"].setChecked(self.zoom is None and self.fit_mode == "shrink")
        by_key["fit_enlarge"].setChecked(self.zoom is None and self.fit_mode == "enlarge")
        if self._shown is None:
            self.status_labels["mode"].setText("")
            return
        scale = self.current_scale()
        title = "ZOOM" if self.zoom is not None else ("REDUCIR" if self.fit_mode == "shrink" else "AJUSTAR")
        self.status_labels["mode"].setText(f"{title} ({scale * 100:.0f}%)")
        self.zoom_slider.blockSignals(True)  # si no, mover el control desde aquí fijaría un zoom manual
        self.zoom_slider.setValue(zoom_to_slider(max(ZOOM_MIN, min(ZOOM_MAX, scale))))
        self.zoom_slider.blockSignals(False)
        scrollable = self.zoom is not None and (self.label.width() > self.scroll.viewport().width()
                                                or self.label.height() > self.scroll.viewport().height())
        self.label.setCursor(Qt.CursorShape.OpenHandCursor if scrollable else Qt.CursorShape.ArrowCursor)

    def step(self, delta: int) -> None:
        if self.paths:
            self.index = (self.index + delta) % len(self.paths)
            self.load_current()

    def go_to(self, index: int) -> None:
        if self.paths:
            self.index = max(0, min(index, len(self.paths) - 1))
            self.load_current()

    def set_zoom(self, factor: float | None) -> None:
        self.zoom = factor
        self._apply_zoom()

    def set_fit(self, mode: str) -> None:
        self.fit_mode = mode
        self.set_zoom(None)

    def change_zoom(self, multiplier: float) -> None:
        if self._shown is None:
            return
        self.set_zoom(max(ZOOM_MIN, min(ZOOM_MAX, self.current_scale() * multiplier)))

    def rotate(self, degrees: int) -> None:
        if self._pixmap is None:
            return
        self.rotation = (self.rotation + degrees) % 360
        self._shown = self._pixmap.transformed(QTransform().rotate(self.rotation), Qt.TransformationMode.SmoothTransformation)
        self._apply_zoom()
        self.set_cropping(self.crop.isVisible())  # sigue recortando, pero la selección ya no señala lo mismo

    # ---------------------------------------------------------------- recorte
    def set_cropping(self, active: bool) -> None:
        active = active and self._shown is not None
        if active:
            if self.timer.isActive():
                self.toggle_slideshow()
            self.crop.image_size = self._shown.size()
            self.crop.setGeometry(self.label.rect())
        self.crop.setVisible(active)
        by_key = self.actions_by_key
        by_key["crop"].setChecked(active)
        by_key["save_crop"].setVisible(active)
        for key in ("rename", "delete", "reveal"):  # dejan sitio en la barra mientras se recorta
            by_key[key].setVisible(not active)
        self.crop.set_selection(QRect())

    def _crop_selection_changed(self) -> None:
        area = self.crop.selection
        self.actions_by_key["save_crop"].setEnabled(not area.isEmpty())
        if area.isEmpty():
            self.status_labels["selection"].setText(tr("Arrastra sobre la imagen para seleccionar") if self.crop.isVisible() else "")
        else:
            self.status_labels["selection"].setText(tr("Selección: {width} x {height}", width=area.width(), height=area.height()))

    def save_crop(self) -> None:
        """Pide dónde guardar la selección como una imagen nueva. El original no cambia."""
        area = QRect(self.crop.selection)
        path = self.current_path
        if area.isEmpty() or not path:
            return
        target, _filter = QFileDialog.getSaveFileName(self, tr("Guardar la selección como imagen nueva"), image_ops.crop_name(path),
                                                      tr("Imágenes (") + " ".join(f"*.{f}" for f in image_ops.writable_formats()) + ")")
        if not target:
            return
        if not os.path.splitext(target)[1]:
            target += os.path.splitext(image_ops.crop_name(path))[1]
        rotation = self.rotation
        run_bg(lambda _sink: image_ops.save_crop(path, area, rotation, target), lambda _result: self._crop_saved(path, target),
               lambda exc: QMessageBox.warning(self, APP_NAME, tr("No se pudo guardar el recorte: {exc}", exc=exc)))

    def _crop_saved(self, source: str, target: str) -> None:
        target = os.path.normpath(target)
        if os.path.normcase(target) == os.path.normcase(os.path.normpath(source)):
            if self.current_path == source:
                self.load_current()
        elif os.path.dirname(target) == os.path.dirname(source) and target not in self.paths and source in self.paths:
            current = self.current_path
            self.paths.insert(self.paths.index(source) + 1, target)  # también se llega a ella con «siguiente»
            self.index = self.paths.index(current)
            self.status_labels["position"].setText(f"{self.index + 1}/{len(self.paths)}")
        if self.current_path == source:
            self.set_cropping(False)
        self.statusBar().showMessage(tr("Recorte guardado: {basename}", basename=os.path.basename(target)), 5000)

    def toggle_slideshow(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
        else:
            self.timer.start()
        self.actions_by_key["slideshow"].setChecked(self.timer.isActive())

    def toggle_fullscreen(self) -> None:
        entering = not self.isFullScreen()
        for bar in (self.menuBar(), self.toolbar, self.statusBar()):
            bar.setVisible(not entering)
        if entering:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        elif self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.actions_by_key["fullscreen"].setChecked(entering)

    # ---------------------------------------------------------------- operaciones sobre el archivo
    def copy_image(self) -> None:
        if self._shown is not None:
            area = self.crop.selection if self.crop.isVisible() else QRect()
            QApplication.clipboard().setPixmap(self._shown if area.isEmpty() else self._shown.copy(area))
            self.statusBar().showMessage(tr("Selección copiada") if not area.isEmpty() else tr("Imagen copiada"), 2000)

    def copy_full_image(self) -> None:
        if self._shown is not None:
            QApplication.clipboard().setPixmap(self._shown)
            self.statusBar().showMessage(tr("Imagen copiada"), 2000)

    def toggle_prompt_panel(self) -> None:
        visible = not self.prompt_dock.isVisible()
        self.prompt_dock.setVisible(visible)
        if visible:
            self.resizeDocks([self.prompt_dock], [420], Qt.Orientation.Horizontal)
        self.actions_by_key["ai_prompt"].setChecked(visible)

    def _show_context_menu(self, global_pos: QPoint) -> None:
        if self._shown is None:
            return
        menu = QMenu(self)
        by_key = self.actions_by_key

        zoom_menu = menu.addMenu(themed("zoom_in"), tr("Zoom"))
        zoom_menu.addAction(by_key["zoom_in"])
        zoom_menu.addAction(by_key["zoom_out"])
        zoom_menu.addAction(by_key["actual"])
        zoom_menu.addAction(by_key["fit_shrink"])
        zoom_menu.addAction(by_key["fit_enlarge"])

        menu.addSeparator()

        has_selection = self.crop.isVisible() and not self.crop.selection.isEmpty()
        if has_selection:
            copy_sel = menu.addAction(themed("copy"), tr("Copiar selección"))
            copy_sel.triggered.connect(self.copy_image)
            copy_all = menu.addAction(themed("copy"), tr("Copiar la imagen completa"))
            copy_all.triggered.connect(self.copy_full_image)
        else:
            menu.addAction(by_key["copy"])

        menu.addAction(by_key["crop"])
        if has_selection:
            menu.addAction(by_key["save_crop"])

        menu.addSeparator()
        menu.addAction(by_key["fullscreen"])

        if by_key["ai_prompt"].isEnabled():
            menu.addSeparator()
            menu.addAction(by_key["ai_prompt"])

        menu.exec(global_pos)

    def closeEvent(self, event) -> None:
        if self.config.get("viewer_window_mode", "remember") == "remember":
            if not self.isFullScreen() and not self.isMinimized():
                geom_str = bytes(self.saveGeometry().toHex()).decode()
                self.config["viewer_geometry"] = geom_str
                if self.host is not None and hasattr(self.host, "config"):
                    self.host.config["viewer_geometry"] = geom_str
        super().closeEvent(event)

    def open_with_system(self) -> None:
        if self.current_path:
            open_externally(self.current_path)

    def reveal_current(self) -> None:
        if self.host is not None and self.current_path:
            self.host.viewer_reveal(self.current_path)

    def rename_current(self) -> None:
        path = self.current_path
        if self.host is None or not path:
            return
        name, accepted = QInputDialog.getText(self, tr("Renombrar"), tr("Nombre nuevo:"), QLineEdit.EchoMode.Normal, os.path.basename(path))
        if not accepted or not name.strip() or name.strip() == os.path.basename(path):
            return
        try:
            self.paths[self.index] = self.host.viewer_rename(path, name.strip())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self.setWindowTitle(f"{self.current_path} — {APP_NAME}")
        self.status_labels["name"].setText(os.path.basename(self.current_path))
        self.host.viewer_image_changed(self.origin, self.current_path)

    def delete_current(self) -> None:
        path = self.current_path
        if self.host is None or not path:
            return
        question = self.host.viewer_delete_question(path)
        if question:
            answer = QMessageBox.question(self, APP_NAME, question, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        error = self.host.viewer_delete(path)
        if error:
            QMessageBox.warning(self, APP_NAME, error)
            return
        del self.paths[self.index]
        if not self.paths:
            self.close()
            return
        self.index %= len(self.paths)
        self.load_current()

    # ---------------------------------------------------------------- ratón y teclado
    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        toolbar = getattr(self, "toolbar", None)
        if toolbar is not None and watched is toolbar:
            if kind == QEvent.Type.ChildAdded and hasattr(event, "child"):
                child = event.child()
                if getattr(child, "objectName", lambda: "")() == "qt_toolbar_ext_button":
                    child.installEventFilter(self)
        elif getattr(watched, "objectName", lambda: "")() == "qt_toolbar_ext_button":
            if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                self._show_toolbar_overflow_menu(watched)
                return True
            if kind == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._show_toolbar_overflow_menu(watched)
                return True

        if kind == QEvent.Type.ContextMenu and watched in (self.label, self.scroll.viewport(), self.scroll):
            self._show_context_menu(event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos())
            return True
        if kind == QEvent.Type.Wheel and watched is self.scroll.viewport():
            return self._wheel(event)
        btn_prev = getattr(self, "btn_side_prev", None)
        btn_next = getattr(self, "btn_side_next", None)
        side_targets = tuple(w for w in (self.label, self.scroll.viewport(), btn_prev, btn_next) if w is not None)
        if watched in side_targets:
            if kind == QEvent.Type.MouseMove:
                vp = self.scroll.viewport()
                global_pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
                pos = vp.mapFromGlobal(global_pos)
                margin = 120
                can_step = len(self.paths) > 1
                near_left = 0 <= pos.x() <= margin and 0 <= pos.y() <= vp.height()
                near_right = vp.width() - margin <= pos.x() <= vp.width() and 0 <= pos.y() <= vp.height()
                if btn_prev is not None:
                    btn_prev.setVisible(can_step and (near_left or btn_prev.geometry().contains(pos)))
                if btn_next is not None:
                    btn_next.setVisible(can_step and (near_right or btn_next.geometry().contains(pos)))
            elif kind == QEvent.Type.Leave:
                vp = self.scroll.viewport()
                cursor_pos = vp.mapFromGlobal(QCursor.pos())
                if not vp.rect().contains(cursor_pos):
                    if btn_prev is not None:
                        btn_prev.hide()
                    if btn_next is not None:
                        btn_next.hide()
            elif kind == QEvent.Type.Resize and watched is self.scroll.viewport():
                vp = self.scroll.viewport()
                if btn_prev is not None and btn_next is not None:
                    y = max(10, (vp.height() - btn_prev.height()) // 2)
                    btn_prev.move(14, y)
                    btn_next.move(vp.width() - btn_next.width() - 14, y)

        if watched is self.label:
            if kind == QEvent.Type.Resize:
                self.crop.setGeometry(self.label.rect())
            elif kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_from = event.globalPosition().toPoint()
                if self.label.cursor().shape() == Qt.CursorShape.OpenHandCursor:
                    self.label.setCursor(Qt.CursorShape.ClosedHandCursor)
            elif kind == QEvent.Type.MouseMove and self._drag_from is not None:
                now = event.globalPosition().toPoint()
                moved = now - self._drag_from
                self._drag_from = now
                self.scroll.horizontalScrollBar().setValue(self.scroll.horizontalScrollBar().value() - moved.x())
                self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().value() - moved.y())
            elif kind == QEvent.Type.MouseButtonRelease:
                self._drag_from = None
                self._update_zoom_controls()
            elif kind == QEvent.Type.MouseButtonDblClick:
                self.set_zoom(None if self.zoom is not None else 1.0)
        return super().eventFilter(watched, event)

    def _wheel(self, event) -> bool:
        """Ctrl + rueda: zoom. Sin Ctrl: desplaza si la imagen no cabe y, si cabe, cambia de imagen."""
        delta = event.angleDelta().y()
        if not delta:
            return False
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.change_zoom(ZOOM_STEP if delta > 0 else 1 / ZOOM_STEP)
            return True
        if self.scroll.verticalScrollBar().maximum() > 0 or self.crop.isVisible():
            return False  # recortando, un giro de la rueda no debe perder la selección
        self.step(-1 if delta > 0 else 1)
        return True

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Space, Qt.Key.Key_PageDown):
            self.step(1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace, Qt.Key.Key_PageUp):
            self.step(-1)
        elif key == Qt.Key.Key_Home:
            self.go_to(0)
        elif key == Qt.Key.Key_End:
            self.go_to(len(self.paths) - 1)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.change_zoom(ZOOM_STEP)
        elif key == Qt.Key.Key_Minus:
            self.change_zoom(1 / ZOOM_STEP)
        elif key == Qt.Key.Key_0:
            self.set_fit("enlarge")
        elif key == Qt.Key.Key_1:
            self.set_zoom(1.0)
        elif key == Qt.Key.Key_2:
            self.set_fit("shrink")
        elif key == Qt.Key.Key_L:
            self.rotate(-90)
        elif key == Qt.Key.Key_R:
            self.rotate(90)
        elif key in (Qt.Key.Key_F, Qt.Key.Key_F11):
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_S:
            self.toggle_slideshow()
        elif key == Qt.Key.Key_F2:
            self.rename_current()
        elif key == Qt.Key.Key_Delete:
            self.delete_current()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.save_crop()
        elif key == Qt.Key.Key_Escape:
            if self.crop.isVisible():
                self.set_cropping(False)
            elif self.isFullScreen():
                self.toggle_fullscreen()
            else:
                self.close()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.zoom is None:
            self._apply_zoom()
