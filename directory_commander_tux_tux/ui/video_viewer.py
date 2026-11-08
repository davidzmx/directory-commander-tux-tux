"""Visor de vídeo en ventana propia: los controles del visor lateral, fotograma a fotograma y guardar un fotograma como PNG.

Se importa al abrir el primer vídeo, porque iniciar Qt Multimedia lleva tiempo.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QByteArray, QEvent, QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtMultimedia import QMediaPlayer, QtVideo, QVideoFrame
from PySide6.QtWidgets import (QFileDialog, QFrame, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QStackedWidget, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..core import ai_metadata, bar_buttons
from ..core.listing import format_size
from . import image_ops, theme
from .ai_prompt_dock import AIPromptDock
from .open_external import open_externally
from .background import run_bg
from .icons import app_icon, themed
from .media_player import MediaPlayer, format_position
from .shell_menu import tidy_separators
from ..core.i18n import tr

SEEK_STEP_MS = 5000


class VideoViewer(QMainWindow):
    """Un clic en el vídeo o Espacio reproduce o pausa; ← → un fotograma, Mayús + ← → cinco segundos, Re Pág y Av Pág cambian de vídeo,
    F pantalla completa, Esc cerrar.

    `host` y `origin` son como en el visor de imágenes."""

    def __init__(self, config, paths: list[str], start: int = 0, parent=None, host=None, origin=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowIcon(app_icon())
        self.host = host
        self.origin = origin
        self.paths = paths
        self.index = max(0, min(start, len(paths) - 1))
        self._was_maximized = False
        self.config = config
        self._apply_initial_geometry()
        self._double_click = False

        self.media = MediaPlayer(config, windowed=True)
        self.media.failed.connect(self._failed)
        if host is not None:
            self.media.customizeRequested.connect(lambda: host.customize_bars(bar_buttons.VIDEO_CONTROLS, self))
        self.media.player.positionChanged.connect(self._position_changed)
        self.media.video.videoSink().videoFrameChanged.connect(self._frame_changed)
        self.media.player.metaDataChanged.connect(self._show_format)
        self.media.video.installEventFilter(self)
        self.message = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setMargin(20)
        self.open_system_button = QPushButton(tr("Abrir con la aplicación del sistema"))
        self.open_system_button.clicked.connect(self.open_with_system)
        self.failure_page = QWidget()
        failure_layout = QVBoxLayout(self.failure_page)
        failure_layout.addStretch(1)
        failure_layout.addWidget(self.message)
        failure_layout.addWidget(self.open_system_button, 0, Qt.AlignmentFlag.AlignHCenter)
        failure_layout.addStretch(1)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.media)
        self.stack.addWidget(self.failure_page)
        self.setCentralWidget(self.stack)

        self.prompt_dock = AIPromptDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.prompt_dock)
        self.resizeDocks([self.prompt_dock], [420], Qt.Orientation.Horizontal)
        self.prompt_dock.hide()
        self.prompt_dock.visibilityChanged.connect(self._on_prompt_dock_visibility_changed)

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
        w = int(self.config.get("viewer_default_width", 1040))
        h = int(self.config.get("viewer_default_height", 720))
        self.resize(w, h)

    def closeEvent(self, event) -> None:
        if self.config.get("viewer_window_mode", "remember") == "remember":
            if not self.isFullScreen() and not self.isMinimized():
                geom_str = bytes(self.saveGeometry().toHex()).decode()
                self.config["viewer_geometry"] = geom_str
                if self.host is not None and hasattr(self.host, "config"):
                    self.host.config["viewer_geometry"] = geom_str
        super().closeEvent(event)

    # ---------------------------------------------------------------- interfaz
    def _action(self, key: str, label: str, icon: str, slot, shortcut: str = "", checkable: bool = False) -> None:
        action = QAction(themed(icon), label, self)
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
        media = self.media
        add("previous", tr("Vídeo anterior"), "goback", lambda: self.step(-1), "PgUp")
        add("next", tr("Vídeo siguiente"), "goforward", lambda: self.step(1), "PgDown")
        add("play", tr("Reproducir o pausar"), "mediaplay", media.toggle_play, "Space")
        add("stop", tr("Detener"), "mediastop", media.stop)
        add("frame_back", tr("Fotograma anterior"), "framestepback", lambda: media.step_frame(-1), "Left")
        add("frame_forward", tr("Fotograma siguiente"), "framestepforward", lambda: media.step_frame(1), "Right")
        add("seek_back", tr("Retroceder 5 segundos"), "", lambda: self.seek_by(-SEEK_STEP_MS), "Shift+Left")
        add("seek_forward", tr("Avanzar 5 segundos"), "", lambda: self.seek_by(SEEK_STEP_MS), "Shift+Right")
        add("save_frame", tr("Guardar fotograma…"), "saveframe", self.save_frame, "Ctrl+S")
        add("ai_prompt", tr("Info de IA"), "comment", self.toggle_prompt_panel, checkable=True)
        add("fullscreen", tr("Pantalla completa"), "fullscreen", self.toggle_fullscreen, "F11", True)
        add("reveal", tr("Mostrar en el panel"), "folder", self.reveal_current)
        add("open_system", tr("Abrir con la aplicación del sistema"), "", self.open_with_system)
        add("close", tr("Cerrar"), "", self.close)
        self.actions_by_key["reveal"].setEnabled(self.host is not None)
        self.actions_by_key["save_frame"].setEnabled(False)
        self.actions_by_key["ai_prompt"].setEnabled(False)
        self.actions_by_key["ai_prompt"].setVisible(False)

    def _build_menus(self) -> None:
        by_key = self.actions_by_key
        bar = self.menuBar()
        for title, keys in ((tr("&Archivo"), ("save_frame", None, "open_system", "reveal", None, "close")),
                            (tr("&Reproducción"), ("play", "stop", None, "frame_back", "frame_forward", None, "seek_back", "seek_forward")),
                            (tr("&Ver"), ("previous", "next", None, "fullscreen", None, "ai_prompt"))):
            menu = bar.addMenu(title)
            for key in keys:
                menu.addSeparator() if key is None else menu.addAction(by_key[key])

    def _build_toolbar(self) -> None:
        self.toolbar = QToolBar(tr("Visor de vídeo"))
        self.toolbar.setMovable(False)
        self.toolbar.installEventFilter(self)
        self.addToolBar(self.toolbar)
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
        customize = menu.addAction(themed("prefs"), tr("Personalizar…"), lambda: self.host.customize_bars(bar_buttons.VIDEO_VIEWER, self))
        customize.setEnabled(self.host is not None)
        return menu

    def _toggle_button_text(self, checked: bool) -> None:
        cfg = getattr(self.host, "config", self.config)
        cfg["viewer_button_text"] = checked
        self.apply_bar_buttons()

    def apply_bar_buttons(self) -> None:
        """Pone en la barra los botones elegidos en las preferencias. Lo que se quite sigue en los menús y con su atajo."""
        by_key = self.actions_by_key
        config = getattr(self.host, "config", self.config)
        show_text = config.get("viewer_button_text", True)
        removed = bar_buttons.hidden(config, bar_buttons.VIDEO_VIEWER)
        self.toolbar.clear()
        keys = list(bar_buttons.arranged(config, bar_buttons.VIDEO_VIEWER))
        if "ai_prompt" in keys and "reveal" in keys:
            keys.remove("ai_prompt")
            keys.insert(keys.index("reveal"), "ai_prompt")
        for key in keys:
            if key == bar_buttons.SEPARATOR:
                self.toolbar.addSeparator()
            elif key not in removed:
                self.toolbar.addAction(by_key[key])
                button = self.toolbar.widgetForAction(by_key[key])
                if isinstance(button, QToolButton):
                    if show_text and key not in ("previous", "next"):
                        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                    else:
                        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tidy_separators(self.toolbar)
        self.media.apply_bar_buttons()
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
        return super().eventFilter(watched, event)

    def _build_status_bar(self) -> None:
        self.status_labels: dict[str, QLabel] = {}
        bar = self.statusBar()
        bar.setSizeGripEnabled(True)
        bar.setStyleSheet("QStatusBar::item { border: none; }")  # los separadores ya son propios
        for position, key in enumerate(("name", "position", "format", "size", "time", "ai")):
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

    def _on_prompt_dock_visibility_changed(self, visible: bool) -> None:
        if "ai_prompt" in self.actions_by_key:
            self.actions_by_key["ai_prompt"].setChecked(visible)

    def toggle_prompt_panel(self) -> None:
        visible = not self.prompt_dock.isVisible()
        self.prompt_dock.setVisible(visible)
        if visible:
            self.resizeDocks([self.prompt_dock], [420], Qt.Orientation.Horizontal)
        cfg = getattr(self.host, "config", self.config)
        cfg["viewer_ai_prompt_open"] = visible
        if self.host is not None and hasattr(self.host, "config"):
            self.host.config["viewer_ai_prompt_open"] = visible
            if hasattr(self.host.config, "save"):
                try:
                    self.host.config.save()
                except OSError:
                    pass
        elif hasattr(cfg, "save"):
            try:
                cfg.save()
            except OSError:
                pass

    def apply_icons(self) -> None:
        for key, icon in self._icons.items():
            self.actions_by_key[key].setIcon(themed(icon))
        self.media.apply_icons()

    # ---------------------------------------------------------------- archivo
    @property
    def current_path(self) -> str:
        return self.paths[self.index] if self.paths else ""

    def load_current(self) -> None:
        path = self.current_path
        self.setWindowTitle(f"{path} — {APP_NAME}")
        self.stack.setCurrentWidget(self.media)
        labels = self.status_labels
        labels["name"].setText(os.path.basename(path))
        labels["position"].setText(f"{self.index + 1}/{len(self.paths)}")
        for key in ("format", "time", "ai"):
            if key in labels:
                labels[key].setText("")
        try:
            labels["size"].setText(format_size(os.path.getsize(path)))
        except OSError:
            labels["size"].setText("")

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
        cfg = getattr(self.host, "config", self.config)
        if has_ai and bool(cfg.get("viewer_ai_prompt_open", False)):
            if not self.prompt_dock.isVisible():
                self.prompt_dock.show()
                self.resizeDocks([self.prompt_dock], [420], Qt.Orientation.Horizontal)
        elif self.prompt_dock.isVisible():
            self.prompt_dock.hide()

        self.actions_by_key["save_frame"].setEnabled(False)
        self.media.load(path, True)
        for key in ("frame_back", "frame_forward"):
            self.actions_by_key[key].setEnabled(self.media.can_step_frames)
        if self.host is not None:
            self.host.viewer_image_changed(self.origin, path)

    def step(self, delta: int) -> None:
        if len(self.paths) > 1:
            self.index = (self.index + delta) % len(self.paths)
            self.load_current()

    def _failed(self, message: str) -> None:
        self.media.clear()
        self.actions_by_key["save_frame"].setEnabled(False)
        if "ai" in self.status_labels:
            self.status_labels["ai"].setText("")
        self.actions_by_key["ai_prompt"].setEnabled(False)
        self.prompt_dock.set_generation(None)
        self.message.setText(tr("No se pudo reproducir {basename}\n{message}", basename=os.path.basename(self.current_path), message=message))
        self.stack.setCurrentWidget(self.failure_page)

    # ---------------------------------------------------------------- estado
    def _frame_changed(self, frame: QVideoFrame) -> None:
        self.actions_by_key["save_frame"].setEnabled(frame.isValid())
        self._show_format()

    def _show_format(self) -> None:
        frame = self.media.current_frame()
        if not frame.isValid():
            return
        rate = 1000000 / self.media.frame_interval_us()
        speed = tr(" · {rate:.4g} fps", rate=rate)
        width, height = frame.width(), frame.height()
        # El giro, del fotograma: el de los metadatos (`QMediaMetaData.Key.Orientation`) es un tipo que PySide6 no sabe convertir y lanza una excepción.
        if frame.rotation() in (QtVideo.Rotation.Clockwise90, QtVideo.Rotation.Clockwise270):  # el vídeo se ve girado
            width, height = height, width
        self.status_labels["format"].setText(f"{width} x {height}{speed}")

    def _position_changed(self, position: int) -> None:
        self.status_labels["time"].setText(format_position(position))  # hasta los segundos: los milisegundos cambiaban sin parar

    def seek_by(self, milliseconds: int) -> None:
        player = self.media.player
        if self.media.has_source:
            # Sin llegar al final exacto: pedirlo detendría el vídeo y lo llevaría al principio.
            player.setPosition(max(0, min(player.position() + milliseconds, player.duration() - 1)))

    # ---------------------------------------------------------------- guardar
    def save_frame(self) -> None:
        """Guarda el fotograma que se ve, a la resolución del vídeo, como una imagen PNG."""
        player = self.media.player
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
        frame = self.media.current_frame()
        if not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            QMessageBox.warning(self, APP_NAME, tr("No se pudo leer el fotograma"))
            return
        target, _filter = QFileDialog.getSaveFileName(self, tr("Guardar fotograma como PNG"),
                                                      image_ops.frame_name(self.current_path, player.position()), tr("Imagen PNG (*.png)"))
        if not target:
            return
        if not target.lower().endswith(".png"):
            target += ".png"
        run_bg(lambda _sink: image_ops.save_frame(image, target),
               lambda _result: self.statusBar().showMessage(tr("Fotograma guardado: {basename}", basename=os.path.basename(target)), 5000),
               lambda exc: QMessageBox.warning(self, APP_NAME, tr("No se pudo guardar el fotograma: {exc}", exc=exc)))

    def open_with_system(self) -> None:
        if self.current_path:
            self.media.player.pause()
            open_externally(self.current_path)

    def reveal_current(self) -> None:
        if self.host is not None and self.current_path:
            self.host.viewer_reveal(self.current_path)

    # ---------------------------------------------------------------- ventana
    def toggle_fullscreen(self) -> None:
        entering = not self.isFullScreen()
        for bar in (self.menuBar(), self.toolbar, self.statusBar()):  # los controles del vídeo se quedan
            bar.setVisible(not entering)
        if entering:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        elif self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.actions_by_key["fullscreen"].setChecked(entering)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.media.video and event.type() in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
            if event.button() == Qt.MouseButton.LeftButton:
                self._video_clicked(event.type() == QEvent.Type.MouseButtonDblClick)
                return True
        return super().eventFilter(watched, event)

    def _video_clicked(self, double: bool) -> None:
        """Un clic reproduce o pausa al momento. El doble clic empieza por un clic ya atendido: se deshace y
        cambia a pantalla completa; al soltar el segundo no hay nada más que hacer."""
        if double:
            self._double_click = True
            self.media.toggle_play()
            self.toggle_fullscreen()
        elif self._double_click:
            self._double_click = False
        else:
            self.media.toggle_play()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_F:
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.toggle_fullscreen()
            else:
                self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self._closing = True
        if hasattr(self, "media"):
            self.media.clear()  # deja de sonar y suelta el archivo
        if self.config.get("viewer_window_mode", "remember") == "remember":
            if not self.isFullScreen() and not self.isMinimized():
                geom_str = bytes(self.saveGeometry().toHex()).decode()
                self.config["viewer_geometry"] = geom_str
                if self.host is not None and hasattr(self.host, "config"):
                    self.host.config["viewer_geometry"] = geom_str
        if self.host is not None and hasattr(self.host, "config") and hasattr(self.host.config, "save"):
            try:
                self.host.config.save()
            except OSError:
                pass
        elif hasattr(self.config, "save"):
            try:
                self.config.save()
            except OSError:
                pass
        super().closeEvent(event)
