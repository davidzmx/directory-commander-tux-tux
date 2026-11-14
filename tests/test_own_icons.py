"""En Windows, el icono propio de un programa o un acceso directo se pide aparte: la lista no espera a Windows para mostrarse."""
import shutil
import sys

import pytest
from PySide6.QtCore import Qt

from directory_commander_tux_tux.core.listing import entry_from_path
from directory_commander_tux_tux.ui import model as model_module
from directory_commander_tux_tux.ui.file_view import FileView
from tests.test_ui import app, wait_until  # noqa: F401 - se usan como fixtures

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="los iconos propios se piden así solo en Windows")


def test_a_program_shows_the_generic_icon_at_once_and_its_own_icon_later(app, tmp_path, monkeypatch):
    shutil.copy(sys.executable, tmp_path / "programa.exe")
    (tmp_path / "LEEME").write_text("x", encoding="utf-8")
    monkeypatch.setattr(model_module, "_own_icons", model_module.OrderedDict())
    asked = []
    original = model_module._read_own_icon
    monkeypatch.setattr(model_module, "_read_own_icon", lambda path: asked.append(path) or original(path))
    view = FileView()
    view.show()
    view.navigate(str(tmp_path))
    assert wait_until(lambda: view.model.rowCount() == 2)
    row = view.model.row_of(str(tmp_path / "programa.exe"))
    index = view.model.index(row, 0)
    generic = model_module._icons.icon(model_module.QFileIconProvider.IconType.File)
    first = index.data(Qt.ItemDataRole.DecorationRole)
    assert first.cacheKey() == generic.cacheKey()  # sin esperar a Windows
    assert wait_until(lambda: (str(tmp_path / "programa.exe"), entry_from_path(str(tmp_path / "programa.exe")).mtime) in model_module._own_icons)
    own = index.data(Qt.ItemDataRole.DecorationRole)
    assert own.cacheKey() != generic.cacheKey() and not own.pixmap(16, 16).isNull()
    index.data(Qt.ItemDataRole.DecorationRole)
    assert asked.count(str(tmp_path / "programa.exe")) == 1  # una vez leído, se recuerda
    assert model_module.has_own_icon(entry_from_path(str(tmp_path / "LEEME")))
    view.close()
