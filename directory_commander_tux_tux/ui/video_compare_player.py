"""
Reproductor Comparativo de Video (Sincronizado)
Inspirado en MyFFmpeg GUI:
- Inspección interactiva de dos videos en paralelo.
- Modo Pantalla Dividida (Split Screen) con cortina/divisor arrastrable y manija central.
- Modo Lado a Lado (Side-by-Side).
- Controles de reproducción: Play/Pausa, línea de tiempo con salto al clic, avance/retroceso por segundo y fotograma.
- Sincronización exacta fotograma a fotograma mediante decodificación FFmpeg con filtro vstack.
- Selección de audio (Silenciado, Video A, Video B) y control de volumen.
- Selector de velocidad (0.5x, 1.0x, 1.5x, 2.0x), modo bucle y pantalla completa.
"""

import os
import sys
import time
import subprocess
from typing import Optional, Tuple, Dict, Any

from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QSlider, QComboBox, QFrame, QStyle, QSizePolicy, QToolTip, QMessageBox, QApplication
)
from PySide6.QtGui import (
    QPainter, QImage, QColor, QPen, QBrush, QFont, QKeySequence,
    QCursor, QPixmap, QIcon, QPainterPath, QPolygonF, QShortcut
)
from PySide6.QtCore import (
    Qt, QRect, QPoint, QSize, QThread, Signal, QTimer, QMutex, QWaitCondition, QPointF
)

from .. import APP_NAME
from ..core import media_info
from ..core.i18n import tr
from .icons import app_icon


def format_timestamp(seconds: float) -> str:
    """Formatea segundos a formato HH:MM:SS o MM:SS."""
    if seconds < 0:
        seconds = 0.0
    s = int(seconds)
    hours = s // 3600
    minutes = (s % 3600) // 60
    secs = s % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def create_volume_icon(muted: bool = False, is_disabled: bool = False, color: str = "#f8fafc", size: int = 22) -> QIcon:
    """Genera un icono vectorial de volumen nítido y adaptable."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    fill_color = "#475569" if is_disabled else ("#ef4444" if muted else color)

    path = QPainterPath()
    path.addRect(2.5, 7.5, 3.5, 7.0)
    poly = QPolygonF([QPointF(6.0, 7.5), QPointF(11.5, 3.5), QPointF(11.5, 18.5), QPointF(6.0, 14.5)])
    path.addPolygon(poly)
    painter.fillPath(path, QColor(fill_color))

    if muted and not is_disabled:
        pen = QPen(QColor("#ef4444"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(4, 4, 18, 18)
    else:
        pen = QPen(QColor(fill_color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(10, 6, 5, 10, -55 * 16, 110 * 16)
        painter.drawArc(11, 3, 8, 16, -55 * 16, 110 * 16)

    painter.end()
    return QIcon(pix)


def create_fullscreen_icon(is_fullscreen: bool = False, color: QColor = QColor("#f8fafc")) -> QIcon:
    """Genera un icono vectorial de pantalla completa nítido."""
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin)
    painter.setPen(pen)

    if not is_fullscreen:
        # Cuatro esquinas expandiéndose hacia afuera: ⛶
        painter.drawLine(4, 9, 4, 4)
        painter.drawLine(4, 4, 9, 4)
        painter.drawLine(15, 4, 20, 4)
        painter.drawLine(20, 4, 20, 9)
        painter.drawLine(4, 15, 4, 20)
        painter.drawLine(4, 20, 9, 20)
        painter.drawLine(15, 20, 20, 20)
        painter.drawLine(20, 20, 20, 15)
    else:
        # Cuatro esquinas contrayéndose hacia adentro: restaurar
        painter.drawLine(8, 4, 8, 8)
        painter.drawLine(8, 8, 4, 8)
        painter.drawLine(16, 4, 16, 8)
        painter.drawLine(16, 8, 20, 8)
        painter.drawLine(8, 20, 8, 16)
        painter.drawLine(8, 16, 4, 16)
        painter.drawLine(16, 20, 16, 16)
        painter.drawLine(16, 16, 20, 16)

    painter.end()
    return QIcon(pix)


class ClickableSlider(QSlider):
    """QSlider que salta inmediatamente a la posición donde el usuario hace clic."""
    clicked_value = Signal(int)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos_x = event.position().x() if hasattr(event, "position") else event.x()
            pos_y = event.position().y() if hasattr(event, "position") else event.y()
            if self.orientation() == Qt.Orientation.Horizontal:
                val = self.minimum() + ((self.maximum() - self.minimum()) * pos_x) / max(1, self.width())
            else:
                val = self.maximum() - ((self.maximum() - self.minimum()) * pos_y) / max(1, self.height())
            val = max(self.minimum(), min(self.maximum(), int(val)))
            self.setValue(val)
            self.clicked_value.emit(val)
        super().mousePressEvent(event)


class CompareVideoCanvas(QWidget):
    """
    Lienzo interactivo de visualización comparativa de video:
    - Modo 'split': Superposición con línea divisoria arrastrable por el usuario.
    - Modo 'side_by_side': Visualización lado a lado.
    """
    divider_moved = Signal(float)  # Ratio de división (0.0 a 1.0)
    play_pause_requested = Signal()  # Clic simple para alternar Reproducir / Pausa

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(480, 270)

        self.image_a: Optional[QImage] = None
        self.image_b: Optional[QImage] = None
        self.mode: str = "split"  # 'split' o 'side_by_side'
        self.split_ratio: float = 0.5  # Divisor en el 50%
        self.dragging_divider: bool = False
        self.show_labels: bool = True
        self.label_a: str = tr("Original (A)")
        self.label_b: str = tr("Procesado (B)")

        # Temporizador para diferenciar clic simple (Play/Pausa) de doble clic (Pantalla Completa)
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click)
        self._press_pos = QPoint()
        self._has_moved_divider = False

        self.video_rect: QRect = QRect()

    def set_images(self, img_a: Optional[QImage], img_b: Optional[QImage]):
        self.image_a = img_a
        self.image_b = img_b
        self.update()

    def set_mode(self, mode: str):
        if mode in ("split", "side_by_side"):
            self.mode = mode
            self.update()

    def set_split_ratio(self, ratio: float):
        self.split_ratio = max(0.02, min(0.98, ratio))
        self.update()

    def _get_divider_x(self) -> int:
        if self.video_rect.isEmpty():
            return int(self.width() * self.split_ratio)
        return int(self.video_rect.x() + self.video_rect.width() * self.split_ratio)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pt = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self._press_pos = pt
            self._has_moved_divider = False
            if self.mode == "split":
                div_x = self._get_divider_x()
                if abs(pt.x() - div_x) <= 20:
                    self.dragging_divider = True
                    self.setCursor(Qt.CursorShape.SplitHCursor)
                else:
                    self.dragging_divider = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pt = event.position().toPoint() if hasattr(event, "position") else event.pos()
        x = pt.x()
        if self.mode == "split":
            div_x = self._get_divider_x()
            if self.dragging_divider:
                if (pt - self._press_pos).manhattanLength() > 3:
                    self._has_moved_divider = True
                if not self.video_rect.isEmpty():
                    ratio = (x - self.video_rect.x()) / max(1, self.video_rect.width())
                else:
                    ratio = x / max(1, self.width())
                self.split_ratio = max(0.02, min(0.98, ratio))
                self.divider_moved.emit(self.split_ratio)
                self.update()
            else:
                if abs(x - div_x) <= 15:
                    self.setCursor(Qt.CursorShape.SplitHCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            was_moving = self._has_moved_divider
            self.dragging_divider = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if not was_moving:
                self._click_timer.start(240)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            top_level = self.window()
            if hasattr(top_level, "_toggle_fullscreen"):
                top_level._toggle_fullscreen()
        super().mouseDoubleClickEvent(event)

    def _on_single_click(self):
        self.play_pause_requested.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        painter.fillRect(self.rect(), QColor("#090d16"))

        if self.image_a is None and self.image_b is None:
            painter.setPen(QColor("#64748b"))
            painter.setFont(QFont("Segoe UI", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("Cargando fotogramas de video…"))
            return

        w_widget = self.width()
        h_widget = self.height()

        sample_img = self.image_a if self.image_a is not None else self.image_b
        vw = sample_img.width() if sample_img else 16
        vh = sample_img.height() if sample_img else 9
        if vw <= 0 or vh <= 0:
            vw, vh = 16, 9

        if self.mode == "split":
            aspect = vw / vh
            target_w = w_widget
            target_h = int(target_w / aspect)
            if target_h > h_widget:
                target_h = h_widget
                target_w = int(target_h * aspect)
            target_x = (w_widget - target_w) // 2
            target_y = (h_widget - target_h) // 2
            self.video_rect = QRect(target_x, target_y, target_w, target_h)

            div_x = int(target_x + target_w * self.split_ratio)

            # Dibujar Video A en el lado izquierdo recortado
            if self.image_a:
                painter.save()
                clip_left = QRect(target_x, target_y, div_x - target_x, target_h)
                painter.setClipRect(clip_left)
                painter.drawImage(self.video_rect, self.image_a)
                painter.restore()

            # Dibujar Video B en el lado derecho recortado
            if self.image_b:
                painter.save()
                clip_right = QRect(div_x, target_y, (target_x + target_w) - div_x, target_h)
                painter.setClipRect(clip_right)
                painter.drawImage(self.video_rect, self.image_b)
                painter.restore()

            # Línea divisoria elegante
            painter.save()
            pen_shadow = QPen(QColor(0, 0, 0, 180), 4)
            painter.setPen(pen_shadow)
            painter.drawLine(div_x, target_y, div_x, target_y + target_h)

            pen_line = QPen(QColor("#38bdf8"), 2)
            painter.setPen(pen_line)
            painter.drawLine(div_x, target_y, div_x, target_y + target_h)

            # Manija central circular con flechas (< | >)
            center_y = target_y + target_h // 2
            radius = 16
            painter.setBrush(QBrush(QColor("#0284c7")))
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawEllipse(QPoint(div_x, center_y), radius, radius)

            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.drawText(QRect(div_x - 14, center_y - 12, 28, 24), Qt.AlignmentFlag.AlignCenter, "◂ ▸")
            painter.restore()

            if self.show_labels:
                self._draw_pill_badge(painter, target_x + 12, target_y + 12, self.label_a, "#2563eb", align_right=False)
                self._draw_pill_badge(painter, target_x + target_w - 12, target_y + 12, self.label_b, "#7c3aed", align_right=True)

        elif self.mode == "side_by_side":
            half_w = w_widget // 2
            aspect = vw / vh

            target_w = half_w - 4
            target_h = int(target_w / aspect)
            if target_h > h_widget:
                target_h = h_widget
                target_w = int(target_h * aspect)

            target_y = (h_widget - target_h) // 2

            # Mitad Izquierda (Video A)
            rect_a = QRect((half_w - target_w) // 2, target_y, target_w, target_h)
            if self.image_a:
                painter.drawImage(rect_a, self.image_a)
            self._draw_pill_badge(painter, rect_a.x() + 8, rect_a.y() + 8, self.label_a, "#2563eb", align_right=False)

            # Mitad Derecha (Video B)
            rect_b = QRect(half_w + (half_w - target_w) // 2, target_y, target_w, target_h)
            if self.image_b:
                painter.drawImage(rect_b, self.image_b)
            self._draw_pill_badge(painter, rect_b.x() + target_w - 8, rect_b.y() + 8, self.label_b, "#7c3aed", align_right=True)

            painter.setPen(QPen(QColor("#334155"), 2))
            painter.drawLine(half_w, 0, half_w, h_widget)

            self.video_rect = QRect(0, 0, w_widget, h_widget)

    def _draw_pill_badge(self, painter: QPainter, x: int, y: int, text: str, accent_color: str, align_right: bool = False):
        painter.save()
        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(text) + 20
        text_h = 24

        badge_x = (x - text_w) if align_right else x
        badge_rect = QRect(badge_x, y, text_w, text_h)
        painter.setBrush(QBrush(QColor(15, 23, 42, 210)))
        painter.setPen(QPen(QColor(accent_color), 1.5))
        painter.drawRoundedRect(badge_rect, 12, 12)

        painter.setPen(QColor("#f8fafc"))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class ComparePlaybackWorker(QThread):
    """
    Hilo de decodificación y sincronización de dos videos con FFmpeg:
    - Sincronización exacta fotograma a fotograma usando vstack.
    - Soporta seek directo, avance continuo, velocidad regulable y audio sincronizado con ffplay.
    """
    frame_ready = Signal(object, object, float)  # img_a, img_b, timestamp
    time_updated = Signal(float)
    state_changed = Signal(bool)  # is_playing
    finished = Signal()
    error = Signal(str)

    def __init__(
        self,
        ffmpeg_path: str,
        ffplay_path: Optional[str],
        video_a: str,
        video_b: str,
        duration: float,
        fps: float = 30.0,
        render_w: int = 960,
        render_h: int = 540,
        parent=None
    ):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.ffplay_path = ffplay_path
        self.video_a = video_a
        self.video_b = video_b
        self.duration = max(0.1, duration)
        self.fps = max(1.0, min(60.0, fps if fps > 0 else 30.0))
        self.render_w = render_w if render_w % 2 == 0 else render_w + 1
        self.render_h = render_h if render_h % 2 == 0 else render_h + 1

        self._is_running = True
        self._is_playing = False
        self._loop = True
        self._current_time = 0.0
        self._speed = 1.0
        self._seek_requested: Optional[float] = None
        self._audio_source: str = "none"  # "none", "a", "b"
        self._volume: int = 100

        self._process: Optional[subprocess.Popen] = None
        self._audio_proc: Optional[subprocess.Popen] = None

        self._mutex = QMutex()
        self._condition = QWaitCondition()

    def play(self):
        self._mutex.lock()
        if self._current_time >= self.duration - 0.05:
            self._current_time = 0.0
            self._seek_requested = 0.0
        self._is_playing = True
        self._condition.wakeAll()
        self._mutex.unlock()
        self.state_changed.emit(True)

    def set_loop(self, loop: bool):
        self._mutex.lock()
        self._loop = bool(loop)
        self._mutex.unlock()

    def pause(self):
        self._mutex.lock()
        self._is_playing = False
        self._stop_audio()
        self._mutex.unlock()
        self.state_changed.emit(False)

    def toggle_play(self):
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def seek(self, target_sec: float):
        self._mutex.lock()
        self._seek_requested = max(0.0, min(self.duration, target_sec))
        self._condition.wakeAll()
        self._mutex.unlock()

    def step(self, delta_sec: float):
        self._mutex.lock()
        target = max(0.0, min(self.duration, self._current_time + delta_sec))
        self._seek_requested = target
        self._is_playing = False
        self._stop_audio()
        self._condition.wakeAll()
        self._mutex.unlock()
        self.state_changed.emit(False)

    def set_speed(self, speed: float):
        self._mutex.lock()
        self._speed = max(0.25, min(4.0, speed))
        self._mutex.unlock()

    def set_audio_source(self, source: str):
        self._mutex.lock()
        self._audio_source = source
        if not self._is_playing or source == "none":
            self._stop_audio()
        else:
            self._restart_audio(self._current_time)
        self._mutex.unlock()

    def set_volume(self, volume: int):
        self._mutex.lock()
        self._volume = max(0, min(100, int(volume)))
        need_restart = self._is_playing and self._audio_source != "none"
        cur_time = self._current_time
        self._mutex.unlock()
        if need_restart:
            self._restart_audio(cur_time)

    def stop(self):
        self._mutex.lock()
        self._is_running = False
        self._is_playing = False
        self._condition.wakeAll()
        self._mutex.unlock()
        self._kill_process()
        self._stop_audio()
        if not self.wait(1000):
            try:
                self.terminate()
                self.wait(500)
            except Exception:
                pass

    def _start_stream(self, start_time: float) -> Optional[subprocess.Popen]:
        self._kill_process()

        w, h = self.render_w, self.render_h
        filter_complex = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:-1:-1:black[v0];"
            f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:-1:-1:black[v1];"
            f"[v0][v1]vstack"
        )

        startupinfo = None
        creationflags = 0
        if sys.platform.startswith("win"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        cmd = [
            self.ffmpeg_path,
            "-ss", f"{start_time:.3f}",
            "-i", self.video_a,
            "-ss", f"{start_time:.3f}",
            "-i", self.video_b,
            "-filter_complex", filter_complex,
            "-f", "image2pipe",
            "-vcodec", "rawvideo",
            "-pix_fmt", "rgb24",
            "-r", f"{self.fps:.2f}",
            "-"
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=creationflags,
                bufsize=w * h * 2 * 3 * 2
            )
            return proc
        except Exception as e:
            self.error.emit(f"Error al iniciar decodificación FFmpeg: {e}")
            return None

    def _kill_process(self):
        if self._process:
            try:
                self._process.terminate()
                self._process.kill()
                self._process.wait(timeout=0.2)
            except Exception:
                pass
            self._process = None

    def _restart_audio(self, start_time: float):
        self._stop_audio()
        if not self.ffplay_path or self._audio_source == "none":
            return

        audio_file = self.video_a if self._audio_source == "a" else self.video_b
        if not os.path.isfile(audio_file):
            return

        startupinfo = None
        creationflags = 0
        if sys.platform.startswith("win"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        cmd = [
            self.ffplay_path,
            "-nodisp",
            "-autoexit",
            "-volume", str(int(self._volume)),
            "-ss", f"{start_time:.3f}",
            audio_file
        ]
        try:
            self._audio_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=creationflags
            )
        except Exception:
            pass

    def _stop_audio(self):
        if self._audio_proc:
            try:
                self._audio_proc.terminate()
                self._audio_proc.kill()
                self._audio_proc.wait(timeout=0.2)
            except Exception:
                pass
            self._audio_proc = None

    def run(self):
        w, h = self.render_w, self.render_h
        frame_bytes_single = w * h * 3
        frame_bytes_total = frame_bytes_single * 2

        self._process = self._start_stream(0.0)
        if self._process and self._process.stdout:
            data = self._process.stdout.read(frame_bytes_total)
            if len(data) == frame_bytes_total:
                img_a = QImage(data[:frame_bytes_single], w, h, w * 3, QImage.Format.Format_RGB888).copy()
                img_b = QImage(data[frame_bytes_single:], w, h, w * 3, QImage.Format.Format_RGB888).copy()
                self.frame_ready.emit(img_a, img_b, 0.0)

        while self._is_running:
            target_seek = None
            self._mutex.lock()
            if self._seek_requested is not None:
                target_seek = self._seek_requested
                self._seek_requested = None
            self._mutex.unlock()

            if target_seek is not None:
                self._current_time = target_seek
                self._process = self._start_stream(target_seek)
                if self._process and self._process.stdout:
                    data = self._process.stdout.read(frame_bytes_total)
                    if len(data) == frame_bytes_total:
                        img_a = QImage(data[:frame_bytes_single], w, h, w * 3, QImage.Format.Format_RGB888).copy()
                        img_b = QImage(data[frame_bytes_single:], w, h, w * 3, QImage.Format.Format_RGB888).copy()
                        self.frame_ready.emit(img_a, img_b, target_seek)
                        self.time_updated.emit(target_seek)

                if self._is_playing:
                    self._restart_audio(target_seek)
                else:
                    self._stop_audio()

            self._mutex.lock()
            playing = self._is_playing
            speed = self._speed
            self._mutex.unlock()

            if not playing:
                self._mutex.lock()
                if self._is_running and not self._is_playing and self._seek_requested is None:
                    self._condition.wait(self._mutex, 60)
                self._mutex.unlock()
                continue

            if self._process is None or self._process.stdout is None:
                self._process = self._start_stream(self._current_time)
                if self._is_playing:
                    self._restart_audio(self._current_time)

            frame_interval = 1.0 / (self.fps * speed)
            t_start = time.time()

            data = self._process.stdout.read(frame_bytes_total)
            if len(data) < frame_bytes_total:
                self._mutex.lock()
                is_loop = self._loop
                self._mutex.unlock()

                if is_loop and self._is_running:
                    self._current_time = 0.0
                    self._process = self._start_stream(0.0)
                    if self._is_playing:
                        self._restart_audio(0.0)
                    self.time_updated.emit(0.0)
                    continue
                else:
                    self._is_playing = False
                    self._current_time = self.duration
                    self._stop_audio()
                    self.state_changed.emit(False)
                    self.finished.emit()
                    continue

            img_a = QImage(data[:frame_bytes_single], w, h, w * 3, QImage.Format.Format_RGB888).copy()
            img_b = QImage(data[frame_bytes_single:], w, h, w * 3, QImage.Format.Format_RGB888).copy()

            self._current_time += 1.0 / self.fps
            if self._current_time > self.duration:
                self._current_time = self.duration

            self.frame_ready.emit(img_a, img_b, self._current_time)
            self.time_updated.emit(self._current_time)

            elapsed = time.time() - t_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0.002:
                time.sleep(sleep_time)

        self._kill_process()
        self._stop_audio()


def get_video_info(path: str, ffmpeg_dir: str = "") -> Dict[str, Any]:
    """Obtiene metadatos clave para la comparación usando media_info."""
    info = media_info.read_media_info(path, ffmpeg_dir)
    res = {
        "width": 0,
        "height": 0,
        "duration": 0.0,
        "fps": 30.0,
        "codec": "",
        "dar": 0.0
    }
    if info:
        res["width"] = info.width
        res["height"] = info.height
        res["duration"] = info.duration
        res["fps"] = info.frame_rate if info.frame_rate > 0 else 30.0
        res["codec"] = info.codec
        if info.width > 0 and info.height > 0:
            res["dar"] = info.width / info.height
    return res


class VideoCompareWindow(QDialog):
    """
    Ventana del Reproductor Comparativo de Video Sincronizado.
    """
    def __init__(self, video_a: str, video_b: str, config=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Reproductor Comparativo de Video - [Sincronizado]"))
        self.setWindowIcon(app_icon())
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setMinimumSize(920, 600)
        self.resize(1120, 720)

        self.video_a = video_a
        self.video_b = video_b
        self.config = config or {}

        ffmpeg_dir = self.config.get("ffmpeg_dir", "")
        self.ffmpeg_path = media_info.find_tool("ffmpeg", ffmpeg_dir) or "ffmpeg"
        self.ffplay_path = media_info.find_tool("ffplay", ffmpeg_dir)

        self.info_a = get_video_info(self.video_a, ffmpeg_dir)
        self.info_b = get_video_info(self.video_b, ffmpeg_dir)
        self.duration = max(self.info_a.get("duration", 0.0), self.info_b.get("duration", 0.0), 1.0)

        candidate_fps = max(self.info_a.get("fps", 0.0), self.info_b.get("fps", 0.0))
        self.fps = candidate_fps if candidate_fps > 0 else 30.0

        self.render_w, self.render_h = self._compute_render_resolution()
        self.is_seeking = False
        self._unmuted_volume = 100

        self._init_ui()
        self._init_shortcuts()
        self._init_worker()

    def _compute_render_resolution(self) -> Tuple[int, int]:
        wa = self.info_a.get("width", 0)
        ha = self.info_a.get("height", 0)
        wb = self.info_b.get("width", 0)
        hb = self.info_b.get("height", 0)

        dar_a = self.info_a.get("dar")
        dar_b = self.info_b.get("dar")

        if dar_a and dar_a > 0:
            aspect = dar_a
        elif wa > 0 and ha > 0:
            aspect = wa / ha
        elif dar_b and dar_b > 0:
            aspect = dar_b
        elif wb > 0 and hb > 0:
            aspect = wb / hb
        else:
            aspect = 16.0 / 9.0

        ref_w = wa if (wa > 0 and ha > 0) else (wb if (wb > 0 and hb > 0) else 1280)
        ref_h = ha if (wa > 0 and ha > 0) else (hb if (wb > 0 and hb > 0) else 720)

        max_w = 960
        max_h = 720

        if ref_w <= max_w and ref_h <= max_h:
            target_w = ref_w
            target_h = int(round(target_w / aspect)) if aspect > 0 else ref_h
        else:
            scale = min(max_w / ref_w, max_h / ref_h)
            target_w = int(round(ref_w * scale))
            target_h = int(round(target_w / aspect)) if aspect > 0 else int(round(ref_h * scale))

        target_w = max(2, (target_w // 2) * 2)
        target_h = max(2, (target_h // 2) * 2)
        return target_w, target_h

    def _init_ui(self):
        self.vol_timer = QTimer(self)
        self.vol_timer.setSingleShot(True)
        self.vol_timer.setInterval(100)
        self.vol_timer.timeout.connect(self._apply_volume_to_worker)

        self.setStyleSheet("""
            QDialog {
                background-color: #0b0f19;
                color: #f8fafc;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # 1. Barra Superior con nombres de archivo e información técnica
        h_top = QHBoxLayout()
        h_top.setSpacing(10)

        name_a = os.path.basename(self.video_a)
        name_b = os.path.basename(self.video_b)

        wa, ha = self.info_a.get('width', 0), self.info_a.get('height', 0)
        wb, hb = self.info_b.get('width', 0), self.info_b.get('height', 0)
        res_a_str = f"{wa}x{ha}" if wa > 0 and ha > 0 else ""
        res_b_str = f"{wb}x{hb}" if wb > 0 and hb > 0 else ""

        def _fmt_fps(val: float) -> str:
            if not val or val <= 0:
                return ""
            return f"{val:.2f}".rstrip("0").rstrip(".") + " fps"

        fps_a_str = _fmt_fps(self.info_a.get("fps", 0.0))
        fps_b_str = _fmt_fps(self.info_b.get("fps", 0.0))
        fps_sync_str = _fmt_fps(self.fps)

        lbl_a_text = f"<b>A ({tr('Original')}):</b> {name_a}"
        details_a = [x for x in [res_a_str, fps_a_str] if x]
        if details_a:
            lbl_a_text += f" <span style='color: #94a3b8; font-size: 11px;'>({' • '.join(details_a)})</span>"
        lbl_a = QLabel(lbl_a_text)
        lbl_a.setStyleSheet("color: #60a5fa; font-size: 12px;")
        h_top.addWidget(lbl_a)

        lbl_sep = QLabel("vs")
        lbl_sep.setStyleSheet("color: #94a3b8; font-weight: bold;")
        h_top.addWidget(lbl_sep)

        lbl_b_text = f"<b>B ({tr('Procesado')}):</b> {name_b}"
        details_b = [x for x in [res_b_str, fps_b_str] if x]
        if details_b:
            lbl_b_text += f" <span style='color: #94a3b8; font-size: 11px;'>({' • '.join(details_b)})</span>"
        lbl_b = QLabel(lbl_b_text)
        lbl_b.setStyleSheet("color: #c084fc; font-size: 12px;")
        h_top.addWidget(lbl_b)

        h_top.addStretch()

        lbl_tech = QLabel(f"Sync: {fps_sync_str}" if fps_sync_str else "")
        lbl_tech.setToolTip(tr("Tasa de refresco maestra del motor de sincronización paralela"))
        lbl_tech.setStyleSheet("color: #94a3b8; font-size: 11px; background: #1e293b; padding: 2px 8px; border-radius: 4px;")
        h_top.addWidget(lbl_tech)

        layout.addLayout(h_top)

        # 2. Lienzo de Video
        self.canvas = CompareVideoCanvas(self)
        pill_a_parts = [x for x in [res_a_str, fps_a_str] if x]
        pill_b_parts = [x for x in [res_b_str, fps_b_str] if x]
        self.canvas.label_a = f"{tr('Original')} (A) • {' • '.join(pill_a_parts)}" if pill_a_parts else f"{tr('Original')} (A)"
        self.canvas.label_b = f"{tr('Procesado')} (B) • {' • '.join(pill_b_parts)}" if pill_b_parts else f"{tr('Procesado')} (B)"
        self.canvas.divider_moved.connect(self._on_divider_moved)
        self.canvas.play_pause_requested.connect(self._on_toggle_play)
        layout.addWidget(self.canvas, stretch=1)

        # 3. Línea de tiempo interactiva (Seek Bar)
        h_timeline = QHBoxLayout()
        h_timeline.setSpacing(8)

        self.lbl_time_cur = QLabel("00:00")
        self.lbl_time_cur.setStyleSheet("color: #f8fafc; font-family: monospace; font-size: 12px; font-weight: bold;")
        h_timeline.addWidget(self.lbl_time_cur)

        self.slider_time = ClickableSlider(Qt.Orientation.Horizontal, self)
        self.slider_time.setRange(0, int(self.duration * 100))
        self.slider_time.setValue(0)
        self.slider_time.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 6px;
                background: #334155;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #3b82f6;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #60a5fa;
                border: 2px solid #ffffff;
                width: 14px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background: #93c5fd;
            }
        """)
        self.slider_time.sliderPressed.connect(self._on_slider_pressed)
        self.slider_time.sliderMoved.connect(self._on_slider_moved)
        self.slider_time.sliderReleased.connect(self._on_slider_released)
        self.slider_time.clicked_value.connect(self._on_slider_clicked)
        h_timeline.addWidget(self.slider_time, stretch=1)

        self.lbl_time_total = QLabel(format_timestamp(self.duration))
        self.lbl_time_total.setStyleSheet("color: #94a3b8; font-family: monospace; font-size: 12px;")
        h_timeline.addWidget(self.lbl_time_total)

        layout.addLayout(h_timeline)

        # 4. Barra Inferior de Controles
        h_controls = QHBoxLayout()
        h_controls.setSpacing(6)
        h_controls.setContentsMargins(0, 0, 0, 0)

        # Botón Retroceder 1 segundo
        self.btn_step_back = QPushButton(self)
        self.btn_step_back.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekBackward))
        self.btn_step_back.setToolTip(tr("Retroceder 1 segundo (Flecha Izquierda)"))
        self.btn_step_back.setFixedSize(30, 28)
        self.btn_step_back.clicked.connect(lambda: self.worker.step(-1.0))
        h_controls.addWidget(self.btn_step_back)

        # Botón Play / Pausa
        self.btn_play = QPushButton(tr("Reproducir"), self)
        self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setMinimumWidth(92)
        self.btn_play.setFixedHeight(28)
        self.btn_play.setStyleSheet(
            "font-weight: bold; font-size: 11px; padding: 4px 8px; "
            "background-color: #2563eb; color: white; border-radius: 4px;"
        )
        self.btn_play.setToolTip(tr("Reproducir / Pausar (Barra Espaciadora)"))
        self.btn_play.clicked.connect(self._on_toggle_play)
        h_controls.addWidget(self.btn_play)

        # Botón Avanzar 1 segundo
        self.btn_step_fwd = QPushButton(self)
        self.btn_step_fwd.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekForward))
        self.btn_step_fwd.setToolTip(tr("Avanzar 1 segundo (Flecha Derecha)"))
        self.btn_step_fwd.setFixedSize(30, 28)
        self.btn_step_fwd.clicked.connect(lambda: self.worker.step(1.0))
        h_controls.addWidget(self.btn_step_fwd)

        # Botón Bucle
        self.btn_loop = QPushButton(tr("Bucle"), self)
        self.btn_loop.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.btn_loop.setCheckable(True)
        self.btn_loop.setChecked(True)
        self.btn_loop.setFixedHeight(28)
        self.btn_loop.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #94a3b8; border: 1px solid #475569;
                border-radius: 4px; padding: 4px 8px; font-size: 11px;
            }
            QPushButton:hover {
                background-color: #334155; color: #f8fafc;
            }
            QPushButton:checked {
                background-color: #0284c7; color: white; font-weight: bold; border: 1px solid #38bdf8;
            }
        """)
        self.btn_loop.setToolTip(tr("Repetición continua (Bucle activado / desactivado) - [L]"))
        self.btn_loop.clicked.connect(self._toggle_loop)
        h_controls.addWidget(self.btn_loop)

        # Separador vertical
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setFixedWidth(1)
        sep1.setStyleSheet("color: #334155;")
        h_controls.addWidget(sep1)

        # Selector de Modo: Dividida vs Lado a Lado
        lbl_mode = QLabel(tr("Modo:"))
        lbl_mode.setStyleSheet("color: #cbd5e1; font-size: 11px; font-weight: bold;")
        h_controls.addWidget(lbl_mode)

        self.btn_mode_split = QPushButton(tr("Dividida"), self)
        self.btn_mode_split.setCheckable(True)
        self.btn_mode_split.setChecked(True)
        self.btn_mode_split.setFixedHeight(28)
        self.btn_mode_split.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #94a3b8; border: 1px solid #475569;
                border-radius: 4px; padding: 4px 8px; font-size: 11px;
            }
            QPushButton:checked {
                background-color: #0284c7; color: white; font-weight: bold; border: 1px solid #38bdf8;
            }
        """)
        self.btn_mode_split.clicked.connect(lambda: self._set_mode("split"))
        h_controls.addWidget(self.btn_mode_split)

        self.btn_mode_sbs = QPushButton(tr("Lado a Lado"), self)
        self.btn_mode_sbs.setCheckable(True)
        self.btn_mode_sbs.setChecked(False)
        self.btn_mode_sbs.setFixedHeight(28)
        self.btn_mode_sbs.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #94a3b8; border: 1px solid #475569;
                border-radius: 4px; padding: 4px 8px; font-size: 11px;
            }
            QPushButton:checked {
                background-color: #0284c7; color: white; font-weight: bold; border: 1px solid #38bdf8;
            }
        """)
        self.btn_mode_sbs.clicked.connect(lambda: self._set_mode("side_by_side"))
        h_controls.addWidget(self.btn_mode_sbs)

        # Botón Centrar
        self.btn_center_divider = QPushButton(tr("Centrar"), self)
        self.btn_center_divider.setToolTip(tr("Centrar línea divisoria al 50%"))
        self.btn_center_divider.setFixedHeight(28)
        self.btn_center_divider.setStyleSheet("font-size: 11px; padding: 4px 8px; color: #cbd5e1; background-color: #1e293b; border: 1px solid #475569; border-radius: 4px;")
        self.btn_center_divider.clicked.connect(lambda: self.canvas.set_split_ratio(0.5))
        h_controls.addWidget(self.btn_center_divider)

        # Indicador porcentaje de divisor
        self.lbl_divider_pct = QLabel("50%")
        self.lbl_divider_pct.setFixedWidth(28)
        self.lbl_divider_pct.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_divider_pct.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 11px;")
        h_controls.addWidget(self.lbl_divider_pct)

        h_controls.addStretch(1)

        # Selector de Audio
        lbl_audio = QLabel(tr("Audio:"))
        lbl_audio.setStyleSheet("color: #cbd5e1; font-size: 11px; font-weight: bold;")
        h_controls.addWidget(lbl_audio)

        self.combo_audio = QComboBox(self)
        self.combo_audio.addItem(tr("Silenciado"), "none")
        self.combo_audio.addItem(f"{tr('Video A')} ({tr('Original')})", "a")
        self.combo_audio.addItem(f"{tr('Video B')} ({tr('Procesado')})", "b")
        self.combo_audio.setFixedSize(160, 28)
        self.combo_audio.setStyleSheet("""
            QComboBox {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e293b;
                color: #f8fafc;
                selection-background-color: #2563eb;
            }
        """)
        self.combo_audio.currentIndexChanged.connect(self._on_audio_changed)
        h_controls.addWidget(self.combo_audio)

        # Control de Volumen
        self.btn_volume = QPushButton(self)
        self.btn_volume.setIcon(create_volume_icon(is_disabled=True))
        self.btn_volume.setToolTip(tr("Silenciar / Reactivar Audio (M)"))
        self.btn_volume.setFixedSize(28, 28)
        self.btn_volume.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; border: 1px solid #475569;
                border-radius: 4px; padding: 2px;
            }
            QPushButton:hover {
                background-color: #334155; border-color: #38bdf8;
            }
            QPushButton:disabled {
                background-color: #0f172a; border-color: #1e293b;
            }
        """)
        self.btn_volume.clicked.connect(self._toggle_mute)
        h_controls.addWidget(self.btn_volume)

        self.slider_volume = ClickableSlider(Qt.Orientation.Horizontal, self)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(100)
        self.slider_volume.setFixedSize(60, 20)
        self.slider_volume.setToolTip(tr("Nivel de volumen (0% - 100%)"))
        self.slider_volume.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 5px;
                background: #334155;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #38bdf8;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid #38bdf8;
                width: 12px;
                height: 12px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 6px;
            }
        """)
        self.slider_volume.valueChanged.connect(self._on_volume_changed)
        self.slider_volume.sliderReleased.connect(self._on_volume_released)
        self.slider_volume.clicked_value.connect(lambda _v: self._on_volume_released())
        h_controls.addWidget(self.slider_volume)

        self.lbl_volume = QLabel("100%", self)
        self.lbl_volume.setFixedWidth(30)
        self.lbl_volume.setStyleSheet("color: #475569; font-size: 11px;")
        h_controls.addWidget(self.lbl_volume)

        self.btn_volume.setEnabled(False)
        self.slider_volume.setEnabled(False)

        # Separador vertical
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFixedWidth(1)
        sep2.setStyleSheet("color: #334155;")
        h_controls.addWidget(sep2)

        # Selector de Velocidad
        self.combo_speed = QComboBox(self)
        self.combo_speed.addItem("0.5x", 0.5)
        self.combo_speed.addItem("1.0x", 1.0)
        self.combo_speed.addItem("1.5x", 1.5)
        self.combo_speed.addItem("2.0x", 2.0)
        self.combo_speed.setCurrentIndex(1)
        self.combo_speed.setFixedSize(62, 28)
        self.combo_speed.setToolTip(tr("Velocidad de reproducción (0.5x - 2.0x)"))
        self.combo_speed.setStyleSheet("""
            QComboBox {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 11px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e293b;
                color: #f8fafc;
                selection-background-color: #2563eb;
            }
        """)
        self.combo_speed.currentIndexChanged.connect(self._on_speed_changed)
        h_controls.addWidget(self.combo_speed)

        # Botón Pantalla Completa
        self.btn_fullscreen = QPushButton(self)
        self.btn_fullscreen.setIcon(create_fullscreen_icon(False))
        self.btn_fullscreen.setIconSize(QSize(20, 20))
        self.btn_fullscreen.setToolTip(tr("Alternar Pantalla Completa (F / F11 / Doble clic)"))
        self.btn_fullscreen.setFixedSize(30, 28)
        self.btn_fullscreen.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 2px;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #38bdf8;
            }
        """)
        self.btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        h_controls.addWidget(self.btn_fullscreen)

        layout.addLayout(h_controls)

    def _init_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._on_toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self.worker.step(-1.0))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self.worker.step(1.0))
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self.worker.step(-1.0 / self.fps))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.worker.step(1.0 / self.fps))
        QShortcut(QKeySequence(Qt.Key.Key_Home), self, lambda: self.worker.seek(0.0))
        QShortcut(QKeySequence(Qt.Key.Key_F), self, self._toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, self._toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_M), self, self._toggle_mute)
        QShortcut(QKeySequence(Qt.Key.Key_V), self, self._toggle_mode_key)
        QShortcut(QKeySequence(Qt.Key.Key_S), self, self._toggle_mode_key)
        QShortcut(QKeySequence(Qt.Key.Key_L), self, self._toggle_loop_shortcut)
        QShortcut(QKeySequence(Qt.Key.Key_C), self, lambda: self.canvas.set_split_ratio(0.5))
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._on_escape)

    def _init_worker(self):
        self.worker = ComparePlaybackWorker(
            ffmpeg_path=self.ffmpeg_path,
            ffplay_path=self.ffplay_path,
            video_a=self.video_a,
            video_b=self.video_b,
            duration=self.duration,
            fps=self.fps,
            render_w=self.render_w,
            render_h=self.render_h,
            parent=self
        )
        self.worker.set_loop(self.btn_loop.isChecked())
        self.worker.frame_ready.connect(self._on_frame_ready)
        self.worker.time_updated.connect(self._on_time_updated)
        self.worker.state_changed.connect(self._on_playback_state_changed)
        self.worker.finished.connect(self._on_playback_finished)
        self.worker.start()

    def _on_frame_ready(self, img_a: QImage, img_b: QImage, _timestamp: float):
        self.canvas.set_images(img_a, img_b)

    def _on_time_updated(self, cur_time: float):
        if not self.is_seeking:
            val = int(cur_time * 100)
            self.slider_time.blockSignals(True)
            self.slider_time.setValue(val)
            self.slider_time.blockSignals(False)
            self.lbl_time_cur.setText(format_timestamp(cur_time))

    def _on_playback_state_changed(self, is_playing: bool):
        if is_playing:
            self.btn_play.setText(tr("Pausar"))
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self.btn_play.setStyleSheet(
                "font-weight: bold; font-size: 12px; padding: 6px 10px; "
                "background-color: #d97706; color: white; border-radius: 4px;"
            )
        else:
            self.btn_play.setText(tr("Reproducir"))
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.btn_play.setStyleSheet(
                "font-weight: bold; font-size: 12px; padding: 6px 10px; "
                "background-color: #2563eb; color: white; border-radius: 4px;"
            )

    def _on_playback_finished(self):
        self._on_playback_state_changed(False)

    def _on_toggle_play(self):
        if not self.worker._is_playing and self.worker._current_time >= self.duration - 0.05:
            self.worker.seek(0.0)
        self.worker.toggle_play()

    def _toggle_loop(self):
        self.worker.set_loop(self.btn_loop.isChecked())

    def _toggle_loop_shortcut(self):
        self.btn_loop.setChecked(not self.btn_loop.isChecked())
        self._toggle_loop()

    def _on_slider_pressed(self):
        self.is_seeking = True

    def _on_slider_moved(self, value: int):
        cur_sec = value / 100.0
        self.lbl_time_cur.setText(format_timestamp(cur_sec))

    def _on_slider_released(self):
        self.is_seeking = False
        target_sec = self.slider_time.value() / 100.0
        self.worker.seek(target_sec)

    def _on_slider_clicked(self, value: int):
        target_sec = value / 100.0
        self.lbl_time_cur.setText(format_timestamp(target_sec))
        self.worker.seek(target_sec)

    def _on_divider_moved(self, ratio: float):
        pct = int(ratio * 100)
        self.lbl_divider_pct.setText(f"{pct}%")

    def _set_mode(self, mode: str):
        if mode == "split":
            self.btn_mode_split.setChecked(True)
            self.btn_mode_sbs.setChecked(False)
            self.btn_center_divider.setVisible(True)
            self.lbl_divider_pct.setVisible(True)
            self.canvas.set_mode("split")
        else:
            self.btn_mode_split.setChecked(False)
            self.btn_mode_sbs.setChecked(True)
            self.btn_center_divider.setVisible(False)
            self.lbl_divider_pct.setVisible(False)
            self.canvas.set_mode("side_by_side")

    def _toggle_mode_key(self):
        if self.canvas.mode == "split":
            self._set_mode("side_by_side")
        else:
            self._set_mode("split")

    def _on_audio_changed(self, index: int):
        source = self.combo_audio.itemData(index)
        self.worker.set_audio_source(source)
        is_active = (source != "none")
        self.btn_volume.setEnabled(is_active)
        self.slider_volume.setEnabled(is_active)
        if is_active:
            is_muted = (self.slider_volume.value() == 0)
            self.btn_volume.setIcon(create_volume_icon(muted=is_muted, is_disabled=False))
            self.lbl_volume.setStyleSheet("color: #38bdf8; font-size: 11px;")
        else:
            self.btn_volume.setIcon(create_volume_icon(is_disabled=True))
            self.lbl_volume.setStyleSheet("color: #475569; font-size: 11px;")

    def _on_volume_changed(self, value: int):
        self.lbl_volume.setText(f"{value}%")
        is_active = self.slider_volume.isEnabled()
        if is_active:
            self.btn_volume.setIcon(create_volume_icon(muted=(value == 0), is_disabled=False))
        else:
            self.btn_volume.setIcon(create_volume_icon(is_disabled=True))
        if value > 0:
            self._unmuted_volume = value
        self.vol_timer.start()

    def _on_volume_released(self):
        self.vol_timer.stop()
        self._apply_volume_to_worker()

    def _apply_volume_to_worker(self):
        val = self.slider_volume.value()
        self.worker.set_volume(val)

    def _toggle_mute(self):
        if not self.slider_volume.isEnabled():
            return
        if self.slider_volume.value() > 0:
            self._unmuted_volume = self.slider_volume.value()
            self.slider_volume.setValue(0)
        else:
            restore_val = getattr(self, "_unmuted_volume", 80) or 80
            self.slider_volume.setValue(restore_val)
        self._apply_volume_to_worker()

    def _on_speed_changed(self, index: int):
        speed = self.combo_speed.itemData(index)
        self.worker.set_speed(speed)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.btn_fullscreen.setIcon(create_fullscreen_icon(False))
        else:
            self.showFullScreen()
            self.btn_fullscreen.setIcon(create_fullscreen_icon(True))

    def _on_escape(self):
        if self.isFullScreen():
            self._toggle_fullscreen()
        else:
            self.close()

    def closeEvent(self, event):
        self.worker.stop()
        super().closeEvent(event)
