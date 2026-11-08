"""Panel lateral con el prompt y metadatos de IA para imágenes y vídeos en el visualizador interno."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..core.ai_metadata import Generation
from ..core.i18n import tr
from .icons import themed


class AIPromptDock(QDockWidget):
    """Panel lateral en el visor de imágenes/vídeos para consultar y copiar el prompt y parámetros de IA."""

    def __init__(self, parent=None) -> None:
        super().__init__(tr("Info de IA"), parent)
        self.setObjectName("aiPromptDock")
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)

        self._generation: Generation | None = None
        self._current_prompt = ""
        self._current_negative = ""
        self._current_workflow = ""

        # Contenedor con scroll
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        self.layout = QVBoxLayout(container)
        self.layout.setContentsMargins(12, 10, 12, 12)
        self.layout.setSpacing(10)

        # Cabecera con herramienta y modelo
        self.header_widget = QWidget()
        header_layout = QVBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        self.tool_label = QLabel()
        self.tool_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.model_label = QLabel()
        self.model_label.setStyleSheet("font-size: 12px; color: #888888;")
        header_layout.addWidget(self.tool_label)
        header_layout.addWidget(self.model_label)
        self.layout.addWidget(self.header_widget)

        # Sección Prompt
        self.prompt_section = QWidget()
        prompt_sec_layout = QVBoxLayout(self.prompt_section)
        prompt_sec_layout.setContentsMargins(0, 0, 0, 0)
        prompt_sec_layout.setSpacing(6)

        prompt_bar = QHBoxLayout()
        prompt_title = QLabel(tr("Prompt:"))
        prompt_title.setStyleSheet("font-weight: bold;")
        prompt_bar.addWidget(prompt_title)
        prompt_bar.addStretch(1)

        self.copy_prompt_btn = QPushButton(themed("copy"), tr("Copiar prompt"))
        self.copy_prompt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_prompt_btn.clicked.connect(self.copy_prompt)
        prompt_bar.addWidget(self.copy_prompt_btn)
        prompt_sec_layout.addLayout(prompt_bar)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setReadOnly(True)
        self.prompt_edit.setMinimumHeight(110)
        self.prompt_edit.setMaximumHeight(240)
        prompt_sec_layout.addWidget(self.prompt_edit)
        self.layout.addWidget(self.prompt_section)

        # Sección Prompt Negativo
        self.negative_section = QWidget()
        negative_sec_layout = QVBoxLayout(self.negative_section)
        negative_sec_layout.setContentsMargins(0, 0, 0, 0)
        negative_sec_layout.setSpacing(6)

        negative_bar = QHBoxLayout()
        negative_title = QLabel(tr("Prompt negativo:"))
        negative_title.setStyleSheet("font-weight: bold;")
        negative_bar.addWidget(negative_title)
        negative_bar.addStretch(1)

        self.copy_negative_btn = QPushButton(themed("copy"), tr("Copiar prompt negativo"))
        self.copy_negative_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_negative_btn.clicked.connect(self.copy_negative)
        negative_bar.addWidget(self.copy_negative_btn)
        negative_sec_layout.addLayout(negative_bar)

        self.negative_edit = QPlainTextEdit()
        self.negative_edit.setReadOnly(True)
        self.negative_edit.setMinimumHeight(80)
        self.negative_edit.setMaximumHeight(160)
        negative_sec_layout.addWidget(self.negative_edit)
        self.layout.addWidget(self.negative_section)

        # Sección Workflow
        self.workflow_section = QWidget()
        wf_layout = QVBoxLayout(self.workflow_section)
        wf_layout.setContentsMargins(0, 0, 0, 0)
        wf_layout.setSpacing(6)

        self.workflow_label = QLabel()
        self.workflow_label.setStyleSheet("font-size: 12px; color: #4dabf7;")
        wf_layout.addWidget(self.workflow_label)

        wf_btns_layout = QHBoxLayout()
        wf_btns_layout.setContentsMargins(0, 0, 0, 0)
        wf_btns_layout.setSpacing(6)

        self.copy_workflow_btn = QPushButton(themed("copy"), tr("Copiar workflow"))
        self.copy_workflow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_workflow_btn.clicked.connect(self.copy_workflow)
        wf_btns_layout.addWidget(self.copy_workflow_btn)

        self.save_workflow_btn = QPushButton(themed("save"), tr("Guardar workflow…"))
        self.save_workflow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_workflow_btn.clicked.connect(self.save_workflow)
        wf_btns_layout.addWidget(self.save_workflow_btn)

        wf_layout.addLayout(wf_btns_layout)
        self.layout.addWidget(self.workflow_section)

        # Sección Parámetros/Ajustes
        self.settings_group = QGroupBox(tr("Parámetros"))
        self.settings_form = QFormLayout(self.settings_group)
        self.settings_form.setContentsMargins(8, 8, 8, 8)
        self.settings_form.setSpacing(6)
        self.layout.addWidget(self.settings_group)

        # Mensaje vacío
        self.empty_label = QLabel(tr("No se encontraron etiquetas de IA en este archivo."))
        self.empty_label.setStyleSheet("color: #888888; font-style: italic; padding: 20px;")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(self.empty_label)

        self.layout.addStretch(1)
        self.scroll.setWidget(container)
        self.setWidget(self.scroll)

    def set_generation(self, gen: Generation | None) -> None:
        self._generation = gen
        has_ai = gen is not None and (gen.has_prompt or gen.workflow)
        self.empty_label.setVisible(not has_ai)
        self.header_widget.setVisible(has_ai)
        self.prompt_section.setVisible(has_ai and bool(gen.prompt))
        self.negative_section.setVisible(has_ai and bool(gen.negative))
        self.workflow_section.setVisible(has_ai and bool(gen.workflow))
        self.settings_group.setVisible(has_ai and bool(gen.settings))

        if not has_ai or gen is None:
            self._current_prompt = ""
            self._current_negative = ""
            self._current_workflow = ""
            return

        tool = gen.tool or tr("IA")
        self.tool_label.setText(f"✨ {tool}")
        if gen.model:
            self.model_label.setText(tr("Modelo: {model}", model=gen.model))
            self.model_label.show()
        else:
            self.model_label.hide()

        self._current_prompt = gen.prompt
        self.prompt_edit.setPlainText(gen.prompt)

        self._current_negative = gen.negative
        self.negative_edit.setPlainText(gen.negative)

        self._current_workflow = gen.workflow_data
        has_wf_data = bool(gen.workflow_data)
        self.copy_workflow_btn.setEnabled(has_wf_data)
        self.save_workflow_btn.setEnabled(has_wf_data)

        if gen.workflow:
            if gen.nodes:
                self.workflow_label.setText(tr("Flujo (workflow): Sí ({count} nodos)", count=gen.nodes))
            else:
                self.workflow_label.setText(tr("Flujo (workflow): Sí"))

        # Limpiar y rellenar formulario de parámetros
        while self.settings_form.count():
            item = self.settings_form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for key, value in gen.settings:
            val_label = QLabel(str(value))
            val_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.settings_form.addRow(tr(f"{key}:"), val_label)

    def copy_prompt(self) -> None:
        if self._current_prompt:
            QApplication.clipboard().setText(self._current_prompt)
            self.copy_prompt_btn.setText(tr("¡Copiado!"))
            QTimer.singleShot(1500, lambda: self.copy_prompt_btn.setText(tr("Copiar prompt")))

    def copy_negative(self) -> None:
        if self._current_negative:
            QApplication.clipboard().setText(self._current_negative)
            self.copy_negative_btn.setText(tr("¡Copiado!"))
            QTimer.singleShot(1500, lambda: self.copy_negative_btn.setText(tr("Copiar prompt negativo")))

    def copy_workflow(self) -> None:
        if self._current_workflow:
            QApplication.clipboard().setText(self._current_workflow)
            self.copy_workflow_btn.setText(tr("¡Copiado!"))
            QTimer.singleShot(1500, lambda: self.copy_workflow_btn.setText(tr("Copiar workflow")))

    def save_workflow(self) -> None:
        if not self._current_workflow:
            return
        path, _filter = QFileDialog.getSaveFileName(self, tr("Guardar workflow como archivo JSON"), "workflow.json",
                                                    tr("Archivos JSON (*.json)"))
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._current_workflow)
            except OSError:
                pass

