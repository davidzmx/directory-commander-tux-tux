"""Ventanita para configurar el nombre y correo del usuario git para el repositorio local."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from ..core import git_tools
from ..core.i18n import tr
from .icons import themed


class GitUserDialog(QDialog):
    """Diálogo para ver y editar el usuario y correo de git para el repositorio local actual."""

    def __init__(self, git: str, folder: str, name: str = "", email: str = "", parent=None) -> None:
        super().__init__(parent)
        self.git = git
        self.folder = folder
        self.setWindowTitle(tr("Configuración de Usuario Git"))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setFixedWidth(460)

        app = QApplication.instance()
        palette = app.palette() if app else self.palette()
        is_dark = palette.window().color().lightness() < 128

        if is_dark:
            bg_dialog = "#1e222d"
            text_color = "#cbd5e1"
            title_color = "#ffffff"
            sub_color = "#94a3b8"
            bg_edit = "#11141c"
            border_edit = "#2d3748"
            text_edit = "#ffffff"
            bg_cancel = "#232936"
            border_cancel = "#334155"
            text_cancel = "#cbd5e1"
            hover_cancel = "#2d3748"
            border_dialog = "none"
        else:
            bg_dialog = "#ffffff"
            text_color = "#1e293b"
            title_color = "#0f172a"
            sub_color = "#64748b"
            bg_edit = "#f8fafc"
            border_edit = "#cbd5e1"
            text_edit = "#0f172a"
            bg_cancel = "#f1f5f9"
            border_cancel = "#cbd5e1"
            text_cancel = "#334155"
            hover_cancel = "#e2e8f0"
            border_dialog = "1px solid #cbd5e1"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg_dialog};
                color: {text_color};
                border: {border_dialog};
                border-radius: 8px;
            }}
            QLabel {{
                color: {text_color};
                font-size: 13px;
            }}
            QLabel#dialogTitle {{
                color: {title_color};
                font-size: 15px;
                font-weight: bold;
            }}
            QLabel#dialogSubtitle {{
                color: {sub_color};
                font-size: 12px;
                margin-bottom: 6px;
            }}
            QLabel#fieldLabel {{
                color: {sub_color};
                font-size: 12px;
                font-weight: 500;
            }}
            QLineEdit {{
                background-color: {bg_edit};
                border: 1px solid {border_edit};
                border-radius: 6px;
                color: {text_edit};
                padding: 7px 10px;
                font-size: 13px;
                selection-background-color: #2563eb;
            }}
            QLineEdit:focus {{
                border: 1px solid #3b82f6;
            }}
            QPushButton#btnCancel {{
                background-color: {bg_cancel};
                color: {text_cancel};
                border: 1px solid {border_cancel};
                border-radius: 6px;
                padding: 7px 18px;
                font-size: 13px;
            }}
            QPushButton#btnCancel:hover {{
                background-color: {hover_cancel};
            }}
            QPushButton#btnSave {{
                background-color: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 7px 20px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton#btnSave:hover {{
                background-color: #1d4ed8;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)

        # Encabezado con icono y título
        header = QHBoxLayout()
        header.setSpacing(10)
        user_icon = QLabel("👤")
        user_icon.setStyleSheet("color: #3b82f6; font-size: 19px;")
        header.addWidget(user_icon)
        title_label = QLabel(tr("Configuración de Usuario Git"))
        title_label.setObjectName("dialogTitle")
        header.addWidget(title_label, 1)

        layout.addLayout(header)

        # Descripción
        desc_label = QLabel(tr("Configura el nombre y correo para los commits en este repositorio local."))
        desc_label.setObjectName("dialogSubtitle")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        layout.addSpacing(4)

        # Campo: user.name
        name_title = QLabel(tr("Nombre de Usuario (user.name)"))
        name_title.setObjectName("fieldLabel")
        layout.addWidget(name_title)
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("user.name")
        layout.addWidget(self.name_edit)

        layout.addSpacing(6)

        # Campo: user.email
        email_title = QLabel(tr("Correo Electrónico (user.email)"))
        email_title.setObjectName("fieldLabel")
        layout.addWidget(email_title)
        self.email_edit = QLineEdit(email)
        self.email_edit.setPlaceholderText("user.email")
        layout.addWidget(self.email_edit)

        layout.addSpacing(14)

        # Botones Cancelar y Guardar en Repositorio
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_cancel = QPushButton(tr("Cancelar"))
        self.btn_cancel.setObjectName("btnCancel")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        btn_row.addStretch(1)

        self.btn_save = QPushButton(tr("Guardar en Repositorio"))
        self.btn_save.setObjectName("btnSave")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self.save)
        btn_row.addWidget(self.btn_save)

        layout.addLayout(btn_row)

    def save(self) -> None:
        name = self.name_edit.text()
        email = self.email_edit.text()
        git_tools.set_user(self.git, self.folder, name, email)
        self.accept()
