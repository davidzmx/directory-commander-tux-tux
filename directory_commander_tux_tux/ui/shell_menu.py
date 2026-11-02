"""Añade a un menú de la aplicación las opciones del sistema y de otros programas instalados."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QLabel, QMenu, QWidgetAction

from ..core.context_menu import OPEN_WITH
from ..core.i18n import tr
from ..core.windows_shell import OPEN_WITH_VERB, ShellItem, clean_items, read_bitmap

ICON_SIZE = 16
LAST_GROUP = object()


def menu_text(text: str) -> str:
    """Un nombre de carpeta como texto de menú: un & suelto se tomaría por una tecla de acceso."""
    return text.replace("&", "&&")


def add_section(menu: QMenu, title: str) -> None:
    """Título de una parte del menú. `addSection` no sirve: con hoja de estilos dibuja solo una línea."""
    label = QLabel(title)
    label.setObjectName("menuSection")
    action = QWidgetAction(menu)
    action.setText(title)
    action.setDefaultWidget(label)
    action.setEnabled(False)  # el teclado y el ratón pasan de largo
    menu.addAction(action)


def bitmap_icon(handle: int) -> QIcon:
    """Icono de una opción. Vacío si el programa no da una imagen que se pueda leer."""
    bitmap = read_bitmap(handle) if handle else None
    if bitmap is None:
        return QIcon()
    width, height, pixels, has_alpha = bitmap
    image_format = QImage.Format.Format_ARGB32_Premultiplied if has_alpha else QImage.Format.Format_RGB32
    image = QImage(pixels, width, height, width * 4, image_format).copy()  # copy: los datos no son de Qt
    if height > ICON_SIZE or width > ICON_SIZE:
        image = image.scaled(ICON_SIZE, ICON_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return QIcon(QPixmap.fromImage(image))


def _insert_items(menu: QMenu, shell, items: list[ShellItem], before, on_error, first=None) -> None:
    """`first` añade opciones propias al principio de los submenús."""
    for item in items:
        if item.separator:
            menu.insertSeparator(before)
        elif item.submenu:
            submenu = QMenu(item.label, menu)
            submenu.setIcon(bitmap_icon(item.bitmap))
            placeholder = submenu.addAction(tr("(vacío)"))
            placeholder.setEnabled(False)
            submenu.aboutToShow.connect(lambda m=submenu, i=item: _fill_submenu(m, shell, i, on_error, first))
            menu.insertMenu(before, submenu)
            submenu.menuAction().setEnabled(item.enabled)
        else:
            action = menu.addAction(bitmap_icon(item.bitmap), item.label)
            action.setEnabled(item.enabled)
            if item.checked:
                action.setCheckable(True)
                action.setChecked(True)
            action.triggered.connect(lambda _checked=False, command=item.command: _invoke(shell, command, on_error))
            if before is not None:
                menu.removeAction(action)
                menu.insertAction(before, action)


def _insert_open_with(menu: QMenu, shell, item: ShellItem, place, on_error, first) -> None:
    """Coloca «Abrir con». Si hay opciones propias y Windows solo da una ventana para elegir programa, hace falta un submenú.
    Es una entrada del programa: va en su idioma aunque Windows esté en otro. En español se deja la de Windows, con su tecla."""
    if tr(OPEN_WITH) != OPEN_WITH:
        item = replace(item, label=tr(OPEN_WITH))
    if first is None or item.submenu:
        _insert_items(menu, shell, [item], place, on_error, first)
        return
    submenu = QMenu(item.label, menu)
    first(submenu)
    action = submenu.addAction(tr("Elegir otra aplicación…"))
    action.triggered.connect(lambda _checked=False, command=item.command: _invoke(shell, command, on_error))
    menu.insertMenu(place, submenu)


def _fill_submenu(submenu: QMenu, shell, item: ShellItem, on_error, first=None) -> None:
    """Muchos programas llenan sus submenús al abrirlos, y algunos tardan: por eso no se hace antes."""
    if submenu.property("filled") or not shell.is_open:
        return
    submenu.setProperty("filled", True)
    shell.prepare_submenu(item.submenu, item.position)
    children = clean_items(shell.read_menu(item.submenu), own_verbs=())
    if children or first is not None:
        submenu.clear()
    if first is not None:
        first(submenu)
    _insert_items(submenu, shell, children, None, on_error)


def _invoke(shell, command: int, on_error) -> None:
    try:
        shell.invoke(command)
    except OSError as exc:  # el programa falló: no debe llevarse por delante la aplicación
        on_error(str(exc))


def tidy_separators(menu) -> None:
    """Quita las líneas del principio, del final y las que quedan juntas, de un menú o una barra."""
    previous_is_line = True
    actions = menu.actions()
    for action in actions:
        if action.isSeparator() and previous_is_line:
            menu.removeAction(action)
        else:
            previous_is_line = action.isSeparator()
    actions = menu.actions()
    if actions and actions[-1].isSeparator():
        menu.removeAction(actions[-1])


def add_shell_items(menu: QMenu, shell, on_error=lambda _message: None, before=LAST_GROUP, hidden_keys=(), open_with_place=None,
                    open_with_first=None) -> None:
    """Coloca las opciones delante de la acción `before`, separadas con líneas. Con None van al final del menú y,
    si no se indica, antes de su último grupo. «Abrir con», con sus programas, sustituye a `open_with_place`."""
    items = clean_items(shell.items(), hidden_keys=hidden_keys)
    open_with = [item for item in items if open_with_place is not None and item.verb.lower() == OPEN_WITH_VERB]
    items = [item for item in items if item not in open_with]
    if items:
        if before is LAST_GROUP:
            separators = [action for action in menu.actions() if action.isSeparator()]
            before = separators[-1] if separators else None
        menu.insertSeparator(before)
        _insert_items(menu, shell, items, before, on_error)
        if before is not None:
            menu.insertSeparator(before)
    if open_with:
        _insert_open_with(menu, shell, open_with[0], open_with_place, on_error, open_with_first)
        menu.removeAction(open_with_place)
    tidy_separators(menu)
