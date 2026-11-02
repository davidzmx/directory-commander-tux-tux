"""Panel inferior de utilidades: trabajos, buscar, sincronizar, duplicados y registro."""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from ..core import fsops, utilities
from ..core.fsops import Collision, OpResult
from ..core.listing import format_size
from .background import run_bg
from .transfer_dialog import TransferDialog
from ..core.i18n import tr

SYNC_LABELS = {"only_a": tr("Solo en origen"), "only_b": tr("Solo en destino"), "newer_a": tr("Más nuevo en origen"),
               "newer_b": tr("Más nuevo en destino"), "differs": tr("Distinto")}


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(table.fontMetrics().height() + 6)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    return table


class JobRow(QWidget):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.label = QLabel(title)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)
        self.cancel = QPushButton(tr("Cancelar"))
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.addWidget(self.label, 2)
        row.addWidget(self.bar, 3)
        row.addWidget(self.cancel)


class JobsPanel(QWidget):
    """Cola de operaciones de archivos. Hay un máximo de dos a la vez."""

    aboutToStart = Signal()
    jobFinished = Signal(str, object)  # título, OpResult

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.running = 0
        self._calls: set = set()
        self.empty = QLabel(tr("No hay operaciones en curso"))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.inner = QVBoxLayout()
        self.inner.setContentsMargins(0, 0, 0, 0)
        self.inner.addWidget(self.empty)
        self.inner.addStretch(1)
        holder = QWidget()
        holder.setLayout(self.inner)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def start(self, title: str, fn, on_done=None, transfer: tuple[str, str, str] | None = None) -> None:
        """`fn(sink)` debe devolver un OpResult. Una excepción se convierte en error visible.
        `transfer` (acción, de, a) muestra la ventana de progreso de copiar o mover."""
        self.aboutToStart.emit()
        row = JobRow(title)
        self.inner.insertWidget(0, row)
        self.empty.hide()
        self.running += 1

        def finish(result: OpResult) -> None:
            if window is not None:
                result.unattended = window.unattended.isChecked()
                window.job_finished()
            self.running -= 1
            self.inner.removeWidget(row)
            row.deleteLater()
            self._calls.discard(call)
            self.empty.setVisible(self.running == 0)
            if on_done:
                on_done(result)
            self.jobFinished.emit(title, result)

        def failed(exc: BaseException) -> None:
            if isinstance(exc, fsops.Cancelled):
                finish(OpResult(cancelled=True))
            else:
                finish(OpResult(errors=[(title, str(exc) or exc.__class__.__name__)]))

        def progress(value) -> None:
            done, total, name = value
            if total > 0:
                row.bar.setRange(0, 1000)
                row.bar.setValue(int(done * 1000 / total))
            row.label.setText(f"{title}: {name}" if name else title)

        window = None
        call = run_bg(fn, finish, failed, progress, self.pool)
        self._calls.add(call)
        row.cancel.clicked.connect(lambda: (call.cancel(), row.cancel.setEnabled(False)))
        if transfer is not None:
            window = TransferDialog(*transfer, call.sink, self.window())
            window.cancelRequested.connect(call.cancel)

    def cancel_all(self) -> None:
        for call in list(self._calls):
            call.cancel()


class LogPanel(QPlainTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setMaximumBlockCount(20000)

    def log(self, text: str) -> None:
        self.appendPlainText(f"[{datetime.now():%H:%M:%S}] {text}")


class SyncPanel(QWidget):
    """Compara dos carpetas. Solo copia en el sentido elegido y nunca borra."""

    runJob = Signal(str, object)
    navigateTo = Signal(str, str)

    def __init__(self, get_pair, parent=None) -> None:
        super().__init__(parent)
        self._get_pair = get_pair
        self._pair: tuple[str, str] | None = None
        self._items: list[utilities.SyncItem] = []
        self.by_content = QCheckBox(tr("Comparar contenido (más lento)"))
        self.go = QPushButton(tr("Comparar paneles"))
        self.to_dest = QPushButton(tr("Copiar origen → destino"), enabled=False)
        self.to_source = QPushButton(tr("Copiar destino → origen"), enabled=False)
        self.summary = QLabel(tr("Compara las carpetas abiertas en los dos paneles"))
        self.results = _table([tr("Elemento"), tr("Estado")])
        self.results.setSortingEnabled(False)
        top = QHBoxLayout()
        top.addWidget(self.by_content)
        top.addStretch(1)
        top.addWidget(self.go)
        top.addWidget(self.to_dest)
        top.addWidget(self.to_source)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.results, 1)
        layout.addWidget(self.summary)
        self.go.clicked.connect(self.start)
        self.to_dest.clicked.connect(lambda: self._copy(True))
        self.to_source.clicked.connect(lambda: self._copy(False))

    def start(self) -> None:
        pair = self._get_pair()
        if pair is None:
            self.summary.setText(tr("⚠ Abre dos paneles para comparar"))
            return
        self._pair = pair
        by_content = self.by_content.isChecked()
        self.go.setEnabled(False)
        self.summary.setText(tr("Comparando…"))
        run_bg(lambda sink: utilities.compare_dirs(*pair, by_content, lambda: sink.is_cancelled),
               self._finished, self._failed)

    def _finished(self, items: list[utilities.SyncItem]) -> None:
        self.go.setEnabled(True)
        self._items = items
        self.results.setRowCount(len(items))
        for row, item in enumerate(items):
            self.results.setItem(row, 0, QTableWidgetItem(item.rel))
            self.results.setItem(row, 1, QTableWidgetItem(SYNC_LABELS[item.status]))
        count_a = sum(i.status in ("only_a", "newer_a") for i in items)
        count_b = sum(i.status in ("only_b", "newer_b") for i in items)
        same = tr("Las carpetas coinciden") if not items else tr("{count} diferencias", count=len(items))
        self.summary.setText(same)
        self.to_dest.setEnabled(count_a > 0)
        self.to_source.setEnabled(count_b > 0)

    def _failed(self, exc: BaseException) -> None:
        self.go.setEnabled(True)
        self.summary.setText(f"⚠ {exc}")

    def _copy(self, to_dest: bool) -> None:
        if self._pair is None:
            return
        a, b = self._pair
        wanted = ("only_a", "newer_a") if to_dest else ("only_b", "newer_b")
        source_root, dest_root = (a, b) if to_dest else (b, a)
        rels = [i.rel for i in self._items if i.status in wanted]
        if not rels:
            return

        def work(sink):
            total = OpResult()
            for rel in rels:
                sink.check()
                target_dir = os.path.dirname(os.path.join(dest_root, rel))
                os.makedirs(target_dir, exist_ok=True)
                part = fsops.transfer([os.path.join(source_root, rel)], target_dir, policy=Collision.REPLACE, sink=sink)
                total.done += part.done
                total.errors.extend(part.errors)
                if part.cancelled:
                    total.cancelled = True
                    break
            return total

        self.runJob.emit(tr("Sincronizar {count} elementos", count=len(rels)), work)
        self.to_dest.setEnabled(False)
        self.to_source.setEnabled(False)


class DupePanel(QWidget):
    runJob = Signal(str, object)
    navigateTo = Signal(str, str)

    def __init__(self, get_roots, trash, parent=None) -> None:
        super().__init__(parent)
        self._get_roots = get_roots
        self._trash = trash
        self._call = None
        self.go = QPushButton(tr("Buscar duplicados en la carpeta actual"))
        self.stop = QPushButton(tr("Detener"), enabled=False)
        self.remove = QPushButton(tr("Enviar selección a la papelera"), enabled=False)
        self.summary = QLabel()
        self.results = _table([tr("Archivo"), tr("Grupo"), tr("Tamaño")])
        top = QHBoxLayout()
        top.addWidget(self.go)
        top.addWidget(self.stop)
        top.addStretch(1)
        top.addWidget(self.remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.results, 1)
        layout.addWidget(self.summary)
        self.go.clicked.connect(self.start)
        self.stop.clicked.connect(lambda: self._call and self._call.cancel())
        self.remove.clicked.connect(self._remove)
        self.results.itemSelectionChanged.connect(
            lambda: self.remove.setEnabled(bool(self.results.selectionModel().selectedRows())))
        self.results.cellDoubleClicked.connect(
            lambda row, _c: self.navigateTo.emit(os.path.dirname(self.results.item(row, 0).text()),
                                                 self.results.item(row, 0).text()))

    def start(self) -> None:
        roots = self._get_roots()
        self.go.setEnabled(False)
        self.stop.setEnabled(True)
        self.summary.setText(tr("Analizando…"))
        self.results.setRowCount(0)
        self._call = run_bg(lambda sink: utilities.find_duplicates(roots, 1, lambda: sink.is_cancelled),
                            self._finished, self._failed)

    def _finished(self, groups: list[list[str]]) -> None:
        self._call = None
        self.go.setEnabled(True)
        self.stop.setEnabled(False)
        self.results.setRowCount(sum(len(g) for g in groups))
        row = wasted = 0
        for number, group in enumerate(groups, 1):
            try:
                size = os.path.getsize(group[0])
            except OSError:
                size = 0
            wasted += size * (len(group) - 1)
            for path in group:
                self.results.setItem(row, 0, QTableWidgetItem(path))
                self.results.setItem(row, 1, QTableWidgetItem(str(number)))
                self.results.setItem(row, 2, QTableWidgetItem(format_size(size)))
                row += 1
        self.summary.setText(tr("{count} grupos de duplicados · {value} recuperables", count=len(groups), value=format_size(wasted)))

    def _failed(self, exc: BaseException) -> None:
        self._call = None
        self.go.setEnabled(True)
        self.stop.setEnabled(False)
        self.summary.setText(f"⚠ {exc}")

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.results.selectionModel().selectedRows()})
        paths = [self.results.item(r, 0).text() for r in rows]
        if not paths:
            return
        groups = {self.results.item(r, 1).text() for r in rows}
        for group in groups:
            members = sum(1 for r in range(self.results.rowCount()) if self.results.item(r, 1).text() == group)
            selected = sum(1 for r in rows if self.results.item(r, 1).text() == group)
            if selected >= members:
                QMessageBox.warning(self, tr("Duplicados"), tr("Has seleccionado todas las copias del grupo {group}. Deja al menos una.", group=group))
                return
        answer = QMessageBox.question(self, tr("Duplicados"), tr("¿Enviar {count} archivos a la papelera?", count=len(paths)))
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.runJob.emit(tr("Eliminar duplicados"), lambda sink: fsops.delete_paths(paths, False, self._trash, sink))
        for row in reversed(rows):
            self.results.removeRow(row)


class UtilityPanel(QTabWidget):
    def __init__(self, jobs: JobsPanel, find: QWidget, sync: SyncPanel, dupes: DupePanel, log: LogPanel, parent=None) -> None:
        super().__init__(parent)
        self.jobs, self.find, self.sync, self.dupes, self.log = jobs, find, sync, dupes, log
        self.addTab(jobs, tr("Trabajos"))
        self.addTab(find, tr("Buscar"))
        self.addTab(sync, tr("Sincronizar"))
        self.addTab(dupes, tr("Duplicados"))
        self.addTab(log, tr("Registro"))
