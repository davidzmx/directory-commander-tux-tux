"""Reproductor de vídeo y audio del visor.

Se importa al abrir el primer archivo multimedia, porque iniciar Qt Multimedia lleva tiempo.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QPolygonF
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QSizePolicy, QSlider, QStackedWidget, QStyle, QToolButton,
                               QVBoxLayout, QWidget)

from ..core import bar_buttons
from ..core.media_info import VIDEO_EXTENSIONS, read_media_info
from . import theme
from .icons import themed
from ..core.i18n import tr

SEEK_MIN_WIDTH = 50
DEFAULT_FRAME_US = 40000
# En estos, el reproductor solo llega a los fotogramas clave: un salto de «un fotograma» movería varios de golpe.
NO_FRAME_STEP_EXTENSIONS = {"ts", "m2ts", "wmv"}
STEP_TIMEOUT_MS = 1500  # si no llega fotograma (final del vídeo), no se queda esperando para siempre


def format_position(milliseconds: int) -> str:
    seconds = max(0, milliseconds) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def frame_index_at(time_us: float, first_frame_us: int, interval_us: float) -> int:
    """Número del fotograma que se ve en ese momento. El pequeño margen es porque los tiempos llegan redondeados."""
    return max(0, math.floor((time_us - first_frame_us) / interval_us + 0.01))


def frame_middle_ms(index: int, first_frame_us: int, interval_us: float) -> int:
    """Milisegundo del centro de un fotograma. El reproductor no sabe avanzar de uno en uno, pero muestra el
    fotograma que contiene la posición pedida; apuntando al centro, el redondeo no cae en el de al lado."""
    return math.floor((first_frame_us + (index + 0.5) * interval_us) / 1000)


class ClickSlider(QSlider):
    """Control dibujado aparte. Pulsar o arrastrar lleva directamente a ese punto."""

    def _go_to(self, event) -> None:
        if self.maximum() > self.minimum():
            # Con el control pulsado, setValue ya emite sliderMoved.
            self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), max(0, int(event.position().x())), self.width()))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self._go_to(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._go_to(event)

    def mouseReleaseEvent(self, event) -> None:
        self.setSliderDown(False)


class SeekSlider(ClickSlider):
    """Barra de progreso con el tiempo escrito encima: transcurrido a la izquierda; restante y total a la derecha."""

    def elapsed_text(self) -> str:
        return format_position(self.value())

    def remaining_text(self) -> str:
        return f"-{format_position(self.maximum() - self.value())} / {format_position(self.maximum())}"

    def sizeHint(self) -> QSize:
        return QSize(120, 22)

    def paintEvent(self, _event) -> None:
        colors = theme.current()
        painter = QPainter(self)
        area = self.rect().adjusted(0, 1, -1, -2)
        painter.fillRect(area, QColor(colors.field))
        if self.maximum() > self.minimum():
            filled = round(area.width() * (self.value() - self.minimum()) / (self.maximum() - self.minimum()))
            painter.fillRect(QRect(area.left(), area.top(), filled, area.height()), QColor(colors.accent))
        painter.setPen(QColor(colors.border))
        painter.drawRect(area)
        painter.setPen(QColor(colors.text))
        inner = area.adjusted(5, 0, -5, 0)
        metrics = self.fontMetrics()
        elapsed, remaining = self.elapsed_text(), self.remaining_text()
        if metrics.horizontalAdvance(elapsed) <= inner.width():  # en poco espacio, lo primero que se quita es lo restante
            painter.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elapsed)
        if metrics.horizontalAdvance(elapsed) + metrics.horizontalAdvance(remaining) + 8 <= inner.width():
            painter.drawText(inner, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, remaining)


class VolumeSlider(ClickSlider):
    """Volumen en forma de cuña, que crece hacia la derecha."""

    def paintEvent(self, _event) -> None:
        colors = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        width, height = self.width() - 1.0, self.height() - 3.0
        wedge = QPolygonF([QPointF(0, height), QPointF(width, height), QPointF(width, 1)])
        painter.setBrush(QColor(colors.border))
        painter.drawPolygon(wedge)
        share = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        painter.setClipRect(QRect(0, 0, round(width * share), self.height()))
        painter.setBrush(QColor(colors.accent))
        painter.drawPolygon(wedge)


class MediaPlayer(QWidget):
    failed = Signal(str)
    customizeRequested = Signal()  # clic derecho en los controles → Personalizar…

    def __init__(self, config, parent=None, windowed: bool = False) -> None:
        """`windowed` es el reproductor de la ventana de vídeo: con fotograma a fotograma y siempre reproduciendo
        al abrir, así que sin Auto; el ajuste de repetir es aparte y empieza activado."""
        super().__init__(parent)
        self.config = config
        self.windowed = windowed
        self.path: str | None = None
        self._resume_at = 0
        self._cover: QPixmap | None = None
        self._steps_left = 0
        self._step_index: int | None = None  # fotograma al que se está saltando; None si no hay salto en curso
        self._first_frame_us: int | None = None  # no siempre es 0: algunos vídeos empiezan un poco después
        self._seen_interval = 0
        self._file_rate: float | None = None
        self._step_timer = QTimer(self, singleShot=True, interval=STEP_TIMEOUT_MS)
        self._step_timer.timeout.connect(self._finish_steps)

        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.player.setVideoOutput(self.video)

        self.cover = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.cover.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.caption = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.caption.setWordWrap(True)
        policy = self.caption.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self.caption.setSizePolicy(policy)
        self.audio_page = QWidget()
        audio_layout = QVBoxLayout(self.audio_page)
        audio_layout.addWidget(self.cover, 1)
        audio_layout.addWidget(self.caption)
        # Mientras llega el primer fotograma: el widget de vídeo seguiría mostrando el último del archivo anterior.
        self.waiting_page = QWidget()
        self.waiting_page.setStyleSheet("background: black;")
        self._waiting_frame = False
        self.video.videoSink().videoFrameChanged.connect(self._frame_arrived)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.video)
        self.pages.addWidget(self.audio_page)
        self.pages.addWidget(self.waiting_page)

        self.seek = SeekSlider(Qt.Orientation.Horizontal)
        self.seek.setToolTip(tr("Posición"))
        self.seek.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.play_button = self._button(tr("Reproducir o pausar"), self.toggle_play)
        self.stop_button = self._button(tr("Detener y volver al principio"), self.stop)
        self.repeat_button = self._button(tr("Repetir"), self._set_repeat)
        self.repeat_button.setCheckable(True)
        self.back_button = self.forward_button = None
        if windowed:
            self.back_button = self._button(tr("Fotograma anterior (←)"), lambda: self.step_frame(-1))
            self.forward_button = self._button(tr("Fotograma siguiente (→)"), lambda: self.step_frame(1))
            self.back_button.setAutoRepeat(True)
            self.forward_button.setAutoRepeat(True)
        self.auto_button = None
        if not windowed:
            self.auto_button = self._button(tr("Reproducir automáticamente al seleccionar un archivo"), self._set_autoplay)
            self.auto_button.setText("Auto")
            self.auto_button.setCheckable(True)
            self.auto_button.setFixedSize(38, 22)
        self.mute_button = self._button(tr("Silenciar"), self._set_muted)
        self.mute_button.setCheckable(True)
        self.volume = VolumeSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedSize(46, 22)
        self.volume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume.setToolTip(tr("Volumen"))
        self.volume.valueChanged.connect(self._set_volume)

        # Los controles no fijan el ancho mínimo del visor: en poco espacio se ocultan los menos necesarios.
        self.controls = QWidget()
        self.controls.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.controls.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.controls.customContextMenuRequested.connect(lambda position: self.build_controls_menu().exec(self.controls.mapToGlobal(position)))
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(2)
        self._arrange_controls()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.controls)

        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(lambda _state: self.apply_icons())
        self.player.mediaStatusChanged.connect(self._status_changed)
        self.player.hasVideoChanged.connect(self._show_page)
        self.player.metaDataChanged.connect(self._metadata_changed)
        self.player.errorOccurred.connect(lambda _error, message: self.failed.emit(message))
        self.apply_icons()

    def build_controls_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction(themed("prefs"), tr("Personalizar…"), self.customizeRequested.emit)
        return menu

    def _button(self, tooltip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setAutoRaise(True)
        button.setToolTip(tooltip)
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(24, 22)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # el teclado debe seguir en la lista de archivos
        button.clicked.connect(slot)
        return button

    # ---------------------------------------------------------------- archivo
    def load(self, path: str, play: bool | None = None) -> None:
        """Abre un archivo y, según `play` o, sin él, según el ajuste Auto, empieza a reproducirlo."""
        self._load_settings()
        self.path = path
        self._resume_at = 0
        self._reset_display()
        self._show_step_availability()
        extension = path.rsplit(".", 1)[-1].lower()
        self.pages.setCurrentWidget(self.video if extension in VIDEO_EXTENSIONS else self.audio_page)
        self._open(play=bool(self.config.get("viewer_autoplay", True)) if play is None else play)

    @property
    def can_step_frames(self) -> bool:
        return bool(self.path) and self.path.rsplit(".", 1)[-1].lower() not in NO_FRAME_STEP_EXTENSIONS

    def _show_step_availability(self) -> None:
        if self.back_button is None:
            return
        for button, label in ((self.back_button, tr("Fotograma anterior (←)")), (self.forward_button, tr("Fotograma siguiente (→)"))):
            button.setEnabled(self.can_step_frames)
            button.setToolTip(label if self.can_step_frames else tr("Este formato no permite ir de fotograma en fotograma"))

    def _open(self, play: bool) -> None:
        if self.pages.currentWidget() is self.video:
            self.pages.setCurrentWidget(self.waiting_page)
        self._waiting_frame = self.pages.currentWidget() is self.waiting_page
        self.player.setSource(QUrl.fromLocalFile(self.path))
        if play:
            self.player.play()
        else:
            self.player.pause()  # carga el archivo y muestra el primer fotograma

    def clear(self) -> None:
        self.path = None
        self._resume_at = 0
        self._unload()

    def release(self) -> None:
        """Suelta el archivo para que se pueda modificar: en Windows queda bloqueado mientras está abierto.
        El botón de reproducir lo vuelve a abrir en el mismo punto."""
        if self.has_source:
            self._resume_at = self.player.position()
            self._unload()

    def _unload(self) -> None:
        self._finish_steps()
        self.player.stop()
        self.player.setSource(QUrl())
        self.video.videoSink().setVideoFrame(QVideoFrame())

    def _frame_arrived(self, frame: QVideoFrame) -> None:
        if not frame.isValid():
            return
        if self._waiting_frame and self.has_source:
            self._waiting_frame = False
            self.pages.setCurrentWidget(self.video)
        if self._first_frame_us is None and not self._resume_at:
            self._first_frame_us = frame.startTime()
        self._seen_interval = max(self._seen_interval, frame.endTime() - frame.startTime())
        if self._step_index is not None:
            self._next_step()

    @property
    def has_source(self) -> bool:
        return not self.player.source().isEmpty()

    def stop(self) -> None:
        self._resume_at = 0
        if self.has_source:
            self.player.stop()

    def toggle_play(self) -> None:
        if not self.has_source:
            if self.path:
                self._open(play=True)
        elif self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    # ---------------------------------------------------------------- fotograma a fotograma
    def current_frame(self) -> QVideoFrame:
        return self.video.videoSink().videoFrame()

    def frame_interval_us(self) -> float:
        """Lo que dura un fotograma. Qt no da la velocidad de todos los formatos: entonces se lee del archivo y,
        si tampoco está ahí, se usa lo que duran los fotogramas vistos."""
        rate = self.player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
        if not isinstance(rate, (int, float)) or rate <= 0:
            if self._file_rate is None and self.path:
                info = read_media_info(self.path, self.config.get("ffmpeg_dir", ""))
                self._file_rate = info.frame_rate if info is not None else 0.0
            rate = self._file_rate or 0.0
        return 1000000 / rate if rate > 0 else self._seen_interval or DEFAULT_FRAME_US

    def step_frame(self, delta: int) -> None:
        """Pausa y salta `delta` fotogramas. Cada salto espera al fotograma anterior: las pulsaciones rápidas se acumulan."""
        if not self.can_step_frames:
            return
        if not self.has_source:
            if self.path:
                self._open(play=False)
            return
        self.player.pause()
        self._steps_left += delta
        if self._step_index is None:
            self._next_step()

    def _next_step(self) -> None:
        frame = self.current_frame()
        if not self._steps_left or not frame.isValid():
            self._finish_steps()
            return
        first = self._first_frame_us or 0
        interval = self.frame_interval_us()
        index = self._step_index
        if index is None:
            index = frame_index_at(max(self.player.position() * 1000, frame.startTime()), first, interval)
        forward = self._steps_left > 0
        index += 1 if forward else -1
        target = frame_middle_ms(index, first, interval)
        # Pedir el final detendría el vídeo y lo llevaría al principio.
        if index < 0 or (forward and target >= self.player.duration()):
            self._finish_steps()
            return
        self._steps_left += -1 if forward else 1
        self._step_index = index
        self._step_timer.start()
        self.player.setPosition(target)

    def _finish_steps(self) -> None:
        self._step_timer.stop()
        self._steps_left = 0
        self._step_index = None

    # ---------------------------------------------------------------- ajustes
    def _load_settings(self) -> None:
        if self.auto_button is not None:
            self.auto_button.setChecked(bool(self.config.get("viewer_autoplay", True)))
        self.repeat_button.setChecked(bool(self.config.get(self._repeat_setting, self.windowed)))
        self._apply_repeat()
        self.mute_button.setChecked(bool(self.config.get("viewer_muted", False)))
        self.audio.setMuted(self.mute_button.isChecked())
        self.volume.setValue(max(0, min(100, int(self.config.get("viewer_volume", 80)))))
        self._apply_volume()
        self.apply_icons()

    def _set_autoplay(self, checked: bool) -> None:
        self.config["viewer_autoplay"] = checked

    @property
    def _repeat_setting(self) -> str:
        return "video_window_repeat" if self.windowed else "viewer_repeat"

    def _set_repeat(self, checked: bool) -> None:
        self.config[self._repeat_setting] = checked
        self._apply_repeat()

    def _apply_repeat(self) -> None:
        loops = QMediaPlayer.Loops.Infinite if self.repeat_button.isChecked() else QMediaPlayer.Loops.Once
        self.player.setLoops(loops.value if hasattr(loops, "value") else loops)

    def _set_muted(self, checked: bool) -> None:
        self.config["viewer_muted"] = checked
        self.audio.setMuted(checked)
        self.apply_icons()

    def _set_volume(self, value: int) -> None:
        self.config["viewer_volume"] = value
        self._apply_volume()

    def _apply_volume(self) -> None:
        # El oído no percibe el volumen de forma lineal: con la curva, el centro del control suena a medio volumen.
        self.audio.setVolume((self.volume.value() / 100) ** 2)

    def apply_icons(self) -> None:
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setIcon(themed("mediapause" if playing else "mediaplay"))
        self.stop_button.setIcon(themed("mediastop"))
        self.repeat_button.setIcon(themed("mediarepeat"))
        if self.back_button is not None:
            self.back_button.setIcon(themed("framestepback"))
            self.forward_button.setIcon(themed("framestepforward"))
        self.mute_button.setIcon(themed("mediamuted" if self.audio.isMuted() else "mediavolume"))

    # ---------------------------------------------------------------- estado
    def _reset_display(self) -> None:
        self._finish_steps()
        self._first_frame_us = None
        self._seen_interval = 0
        self._file_rate = None
        self._cover = None
        self.cover.clear()
        self.caption.clear()
        self.seek.setRange(0, 0)

    def _fit_controls(self) -> None:
        """Muestra, por orden de utilidad, los controles que caben junto a la barra de progreso.
        Reproducir no se oculta nunca."""
        available = self.controls.width()  # se lee antes: mostrar un control reactiva el diseño de los contenedores
        spacing = self.controls.layout().spacing()
        removed = bar_buttons.hidden(self.config, bar_buttons.VIDEO_CONTROLS if self.windowed else bar_buttons.PLAYER_CONTROLS)
        used = SEEK_MIN_WIDTH
        for key, widget in (("play", self.play_button), ("stop", self.stop_button), ("frame_back", self.back_button),
                            ("frame_forward", self.forward_button), ("repeat", self.repeat_button), ("mute", self.mute_button),
                            ("auto", self.auto_button), ("volume", self.volume)):
            if widget is None:
                continue
            if key in removed:
                widget.setVisible(False)
                continue
            used += min(widget.sizeHint().width(), widget.maximumWidth()) + spacing
            widget.setVisible(key == "play" or used <= available)

    def _arrange_controls(self) -> None:
        """Coloca los controles en el orden elegido. La barra de progreso se lleva el espacio que sobre."""
        widgets = {"play": self.play_button, "stop": self.stop_button, "repeat": self.repeat_button, "frame_back": self.back_button,
                   "frame_forward": self.forward_button, "auto": self.auto_button, "mute": self.mute_button, "volume": self.volume}
        layout = self.controls.layout()
        while layout.count():
            layout.takeAt(0)
        for key in bar_buttons.arranged(self.config, bar_buttons.VIDEO_CONTROLS if self.windowed else bar_buttons.PLAYER_CONTROLS):
            if key == bar_buttons.SEEK:
                layout.addWidget(self.seek, 1)
            elif widgets.get(key) is not None:
                layout.addWidget(widgets[key])

    def apply_bar_buttons(self) -> None:
        self._arrange_controls()
        self._fit_controls()

    def _position_changed(self, position: int) -> None:
        if not self.seek.isSliderDown():
            self.seek.setValue(position)

    def _duration_changed(self, duration: int) -> None:
        self.seek.setRange(0, duration)

    def _status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._resume_at and status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            self.player.setPosition(self._resume_at)
            self._resume_at = 0

    def _show_page(self, has_video: bool) -> None:
        if not has_video:
            self._waiting_frame = False
            self.pages.setCurrentWidget(self.audio_page)
        elif not self._waiting_frame:
            self.pages.setCurrentWidget(self.video)

    def _metadata_changed(self) -> None:
        meta = self.player.metaData()
        parts = [meta.stringValue(key) for key in (QMediaMetaData.Key.Title, QMediaMetaData.Key.ContributingArtist,
                                                   QMediaMetaData.Key.AlbumTitle)]
        self.caption.setText("\n".join(part for part in parts if part))
        image = meta.value(QMediaMetaData.Key.CoverArtImage) or meta.value(QMediaMetaData.Key.ThumbnailImage)
        self._cover = QPixmap.fromImage(image) if image is not None and not image.isNull() else None
        self._fit_cover()

    def _fit_cover(self) -> None:
        if self._cover is not None:
            self.cover.setPixmap(self._cover.scaled(self.cover.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                    Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_cover()
        self._fit_controls()
