"""Qué hacer con un archivo que ya existe en el destino: reemplazarlo, dar otro nombre al nuevo, omitirlo…"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFileIconProvider, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout)

from ..core import fsops
from ..core.fsops import Action, Answer
from ..core.i18n import tr
from ..core.listing import entry_from_path, format_size
from .icons import themed
from .model import icon_for

_THUMB = QSize(84, 84)
_THUMB_MAX_BYTES = 64 * 1024 * 1024  # se lee en el hilo de la interfaz: una imagen enorme haría esperar la pregunta
_TEXT_SAMPLE = 2048
_TEXT_LINES = 9
_PATH_WIDTH = 430

_SHIFT = Qt.KeyboardModifier.ShiftModifier
_CTRL = Qt.KeyboardModifier.ControlModifier
_ALT = Qt.KeyboardModifier.AltModifier
_REPLACE_KEYS = {_SHIFT: (Action.REPLACE, True), _CTRL: (Action.RENAME_NEW, False), _CTRL | _SHIFT: (Action.RENAME_NEW, True),
                 _ALT | _CTRL: (Action.RENAME_OLD, False), _ALT | _SHIFT: (Action.RENAME_OLD, True)}
_SKIP_KEYS = {_SHIFT: (Action.SKIP, True), _CTRL: (Action.SKIP_IDENTICAL, False)}


@dataclass(frozen=True)
class _Info:
    icon: object
    size: int | None
    modified: float
    kind: str


def _describe(path: str) -> _Info:
    try:
        entry = entry_from_path(path)
    except OSError:  # un miembro de un comprimido: de él solo se puede saber el tamaño
        try:
            size = fsops.tree_size(path)[0]
        except OSError:
            size = None
        return _Info(QFileIconProvider().icon(QFileIconProvider.IconType.File), size, 0.0, tr("Archivo"))
    return _Info(icon_for(entry), entry.size, entry.mtime, entry.type_label)


def _decode(sample: bytes) -> str:
    try:
        sample[:-3].decode("utf-8")  # la muestra puede acabar a mitad de un carácter
    except UnicodeDecodeError:
        return sample.decode("latin-1")  # un archivo en otra codificación: que los acentos no salgan como rombos
    return sample.decode("utf-8", "ignore")


def _text_page(path: str) -> QPixmap | None:
    """Las primeras líneas de un archivo de texto, como una hoja pequeña. None si no parece texto."""
    with open(path, "rb") as handle:
        sample = handle.read(_TEXT_SAMPLE)
    if not sample or b"\0" in sample:
        return None
    page = QPixmap(_THUMB)
    page.fill(QColor("#ffffff"))
    painter = QPainter(page)
    font = QFont(painter.font())
    font.setPixelSize(7)
    painter.setFont(font)
    painter.setPen(QColor("#1f2124"))
    lines = _decode(sample).splitlines()[:_TEXT_LINES]
    painter.drawText(QRectF(page.rect()).adjusted(3, 2, -2, -2), Qt.TextFlag.TextDontClip | Qt.AlignmentFlag.AlignLeft, "\n".join(lines))
    painter.end()
    return page


def _thumbnail(path: str, info: _Info) -> QPixmap:
    try:
        if os.path.isfile(path) and os.path.getsize(path) <= _THUMB_MAX_BYTES:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            if reader.canRead():
                if reader.size().isValid():
                    reader.setScaledSize(reader.size().scaled(_THUMB, Qt.AspectRatioMode.KeepAspectRatio))
                image = reader.read()
                if not image.isNull():
                    return QPixmap.fromImage(image)
            page = _text_page(path)
            if page is not None:
                return page
    except OSError:
        pass
    return info.icon.pixmap(48, 48)


def _size_text(size: int | None) -> str:
    return "—" if size is None else tr("{size} ({bytes} bytes)", size=format_size(size), bytes=f"{size:,}")


def _date_text(modified: float, other: float) -> str:
    if not modified:
        return "—"
    text = datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M:%S")
    if not other or abs(modified - other) <= fsops.MTIME_TOLERANCE:
        return text
    return f"{text}  ({tr('más reciente') if modified > other else tr('más antiguo')})"


def _path_label(path: str) -> QLabel:
    label = QLabel()
    label.setText(label.fontMetrics().elidedText(path, Qt.TextElideMode.ElideMiddle, _PATH_WIDTH))
    label.setToolTip(path)
    return label


class ReplaceDialog(QDialog):
    """Pregunta por un archivo. En `answer` queda lo elegido; None si se cancela toda la operación."""

    def __init__(self, src: str, dest: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Confirmar reemplazo"))
        self.answer: Answer | None = None
        self._dest = dest
        self._name = os.path.basename(dest)
        existing, new = _describe(dest), _describe(src)

        self.name_edit = QLineEdit(self._name)
        self.name_edit.textChanged.connect(self._name_changed)
        self.notice = QLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.hide()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("El destino ya contiene un archivo llamado «{name}»", name=self._name)))
        layout.addWidget(self._section(tr("Existente:"), dest, existing, new, tr("En:"), QLabel(self._name)))
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)
        layout.addWidget(self._section(tr("Nuevo:"), src, new, existing, tr("De:"), self.name_edit))
        layout.addWidget(self.notice)

        self.replace_button = QPushButton(tr("Reemplazar"))
        self.replace_button.setDefault(True)
        self.replace_button.clicked.connect(self._replace_clicked)
        self.replace_menu = QMenu(self)
        for text, keys, action, to_all in ((tr("Reemplazar todos"), tr("Mayús"), Action.REPLACE, True),
                                           (tr("Renombrar el nuevo"), tr("Ctrl"), Action.RENAME_NEW, False),
                                           (tr("Renombrar los nuevos (todos)"), tr("Ctrl+Mayús"), Action.RENAME_NEW, True),
                                           (tr("Renombrar el existente"), tr("Alt+Ctrl"), Action.RENAME_OLD, False),
                                           (tr("Renombrar los existentes (todos)"), tr("Alt+Mayús"), Action.RENAME_OLD, True),
                                           (tr("Conservar el más reciente"), "", Action.KEEP_NEWER, False),
                                           (tr("Conservar el más reciente (todos)"), "", Action.KEEP_NEWER, True)):
            self._add_choice(self.replace_menu, text, keys, action, to_all)
        self.skip_button = QPushButton(tr("Omitir"))
        self.skip_button.clicked.connect(self._skip_clicked)
        self.skip_menu = QMenu(self)
        for text, keys, action, to_all in ((tr("Omitir todos"), tr("Mayús"), Action.SKIP, True),
                                           (tr("Omitir los idénticos"), tr("Ctrl"), Action.SKIP_IDENTICAL, False)):
            self._add_choice(self.skip_menu, text, keys, action, to_all)
        cancel = QPushButton(tr("Cancelar"))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addLayout(self._split_button(self.replace_button, self.replace_menu, tr("Más formas de reemplazar o renombrar")), 1)
        buttons.addLayout(self._split_button(self.skip_button, self.skip_menu, tr("Más formas de omitir")), 1)
        buttons.addWidget(cancel, 1)
        layout.addLayout(buttons)

        stem = os.path.splitext(self._name)[0] or self._name
        self.name_edit.setSelection(0, len(stem))
        self.name_edit.setFocus()

    def _section(self, title: str, path: str, info: _Info, other: _Info, folder_title: str, name_widget) -> QFrame:
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(QLabel(title), 0, 0, 1, 3)
        thumb = QLabel()
        thumb.setFixedSize(_THUMB)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setPixmap(_thumbnail(path, info))
        grid.addWidget(thumb, 1, 0, 5, 1, Qt.AlignmentFlag.AlignTop)
        rows = ((tr("Nombre:"), name_widget), (folder_title, _path_label(os.path.dirname(path))),
                (tr("Tamaño:"), QLabel(_size_text(info.size))), (tr("Fecha:"), QLabel(_date_text(info.modified, other.modified))),
                (tr("Tipo:"), QLabel(info.kind)))
        for row, (label, value) in enumerate(rows, 1):
            grid.addWidget(QLabel(label), row, 1)
            grid.addWidget(value, row, 2)
        grid.setColumnStretch(2, 1)
        return frame

    def _add_choice(self, menu: QMenu, text: str, keys: str, action: Action, to_all: bool) -> None:
        menu.addAction(f"{text}\t{keys}" if keys else text, lambda: self._finish(action, to_all))

    def _split_button(self, button: QPushButton, menu: QMenu, tip: str) -> QHBoxLayout:
        arrow = QToolButton()
        arrow.setObjectName("splitArrow")
        arrow.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        arrow.setIcon(themed("menu-arrow"))
        arrow.setToolTip(tip)
        arrow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        arrow.setMenu(menu)
        row = QHBoxLayout()
        row.setSpacing(0)
        row.addWidget(button, 1)
        row.addWidget(arrow)
        return row

    def _new_name(self) -> str | None:
        """El nombre escrito, si es otro y se puede usar."""
        name = self.name_edit.text().strip()
        if name == self._name or fsops.validate_name(name):
            return None
        return None if os.path.lexists(os.path.join(os.path.dirname(self._dest), name)) else name

    def _name_changed(self, _text: str) -> None:
        name = self.name_edit.text().strip()
        problem = ""
        if name != self._name:
            problem = fsops.validate_name(name) or ""
            if not problem and os.path.lexists(os.path.join(os.path.dirname(self._dest), name)):
                problem = tr("Ya existe un archivo con ese nombre")
        self.notice.setText(problem)
        self.notice.setVisible(bool(problem))
        self.replace_button.setEnabled(not problem)
        self.replace_button.setText(tr("Reemplazar") if name == self._name else tr("Renombrar"))

    @staticmethod
    def _keys() -> Qt.KeyboardModifier:
        return QApplication.keyboardModifiers() & (_SHIFT | _CTRL | _ALT)

    def _replace_clicked(self) -> None:
        if self.name_edit.text().strip() != self._name:
            self._finish(Action.RENAME_NEW, False)
            return
        self._finish(*_REPLACE_KEYS.get(self._keys(), (Action.REPLACE, False)))

    def _skip_clicked(self) -> None:
        self._finish(*_SKIP_KEYS.get(self._keys(), (Action.SKIP, False)))

    def _finish(self, action: Action, to_all: bool) -> None:
        name = self._new_name() if action is Action.RENAME_NEW and not to_all else None
        self.answer = Answer(action, to_all, name)
        self.accept()
