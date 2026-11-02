"""Comandos adicionales de la ventana principal, separados para que `main_window` siga siendo legible."""
from __future__ import annotations

import json
import os
import time
import zipfile

from PySide6.QtCore import QStorageInfo, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog,
                               QLabel, QMenu, QMessageBox, QSizePolicy, QToolBar, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..core import archive, favorites, frequent, fsops, settings_io
from ..core.fsops import Collision, OpResult
from ..core.listing import COMPUTER_PATH, path_states, listing_text, location_label
from . import image_ops, theme
from .background import run_bg
from .icons import themed
from .model import COLUMNS, IMAGE_EXTENSIONS
from ..core.i18n import tr

COMMAND_POINTS = {"createfolder": "create_folder", "delete": "delete", "rename": "rename", "setattr": "attributes", "getsizes": "sizes",
                  "show": "show", "run": "run", "cli": "run", "split": "split_join", "join": "split_join"}
NEED_SELECTION = {"delete", "rename", "setattr", "split", "join"}
SORT_COLUMNS = {"name": "name", "size": "size", "sizeauto": "size", "sizekb": "size", "disksize": "size",
                "disksizeauto": "size", "type": "type", "modified": "modified", "date": "modified", "attr": "attrs",
                "attrs": "attrs"}
MEGABYTE = 1024 * 1024


def _turn(value: str, current: bool) -> bool:
    """Interpreta `Toggle`, `togglesmart`, `Toggle,NameCol`, `On` u `Off`."""
    word = value.split(",")[0].strip().lower()
    if word.startswith("toggle"):
        return not current
    return word in ("on", "yes", "1")


def _is_inside(path: str, folder: str) -> bool:
    inside = os.path.normcase(os.path.normpath(path))
    outer = os.path.normcase(os.path.normpath(folder))
    return inside.startswith(outer.rstrip("\\/") + os.sep)


def _only_new(nodes: list, known: set[str]) -> list:
    """Lo importado sin lo que ya está. Una rama que se queda sin favoritos no se trae."""
    result = []
    for node in nodes:
        what = favorites.kind(node)
        if what in ("folder", "file"):
            if node["path"] not in known:
                known.add(node["path"])
                result.append(node)
        elif what == "branch":
            children = _only_new(node["children"], known)
            if children:
                result.append({**node, "children": children})
        else:
            result.append(node)
    return result


class ExtraCommands:
    # ---------------------------------------------------------------- opciones de Set
    def _set_sortby(self, value: str) -> None:
        key = value.lower()
        if key == "sortlist":
            menu = QMenu(self)
            for name, label in (("name", tr("Nombre")), ("size", tr("Tamaño")), ("type", tr("Tipo")), ("modified", tr("Modificación")),
                                ("attrs", tr("Atributos"))):
                menu.addAction(label, lambda n=name: self._sort_source(n))
            menu.addSeparator()
            menu.addAction(tr("Invertir el orden"), lambda: self._set_sortreverse("toggle"))
            menu.exec(self.cursor().pos())
            return
        column = SORT_COLUMNS.get(key)
        if column is None:
            raise ValueError(tr("Columna desconocida: {value}", value=value))
        self._sort_source(column)

    def _sort_source(self, column: str) -> None:
        index = next(i for i, (key, _label) in enumerate(COLUMNS) if key == column)
        header = self.source.details.horizontalHeader()
        header.setSortIndicator(index, header.sortIndicatorOrder())

    def _set_sortreverse(self, value: str) -> None:
        header = self.source.details.horizontalHeader()
        descending = header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
        wanted = _turn(value, descending)
        header.setSortIndicator(header.sortIndicatorSection(),
                                Qt.SortOrder.DescendingOrder if wanted else Qt.SortOrder.AscendingOrder)

    def _set_statusbar(self, value: str) -> None:
        for view in self.all_views():
            view.status.setVisible(_turn(value, view.status.isVisibleTo(view)))

    def _set_navlock(self, value: str) -> None:
        self.nav_lock = _turn(value, self.nav_lock)
        self.statusBar().showMessage(tr("Bloqueo de navegación activado") if self.nav_lock else tr("Bloqueo de navegación desactivado"), 4000)

    def _follow_navigation(self, view, previous: str) -> None:
        """Con el bloqueo activo, el otro panel repite el mismo movimiento relativo."""
        if not self.nav_lock or self._following or view is not self.source or self.dest is None or not previous:
            return
        try:
            relative = os.path.relpath(view.path, previous)
        except ValueError:
            return  # otra unidad: no hay movimiento relativo que repetir
        target = os.path.normpath(os.path.join(self.dest.path, relative))
        if os.path.isdir(target):
            self._following = True
            try:
                self.dest.navigate(target)
            finally:
                self._following = False

    def _set_delrecyclebin(self, value: str) -> None:
        self.config["use_trash"] = _turn(value, self.config.get("use_trash", True))

    def _set_delrecycleconfirm(self, value: str) -> None:
        self.config["confirm_delete"] = _turn(value, self.config.get("confirm_delete", True))

    def _set_state(self, value: str) -> None:
        if self.dest is None:
            return
        wanted = value.lower()
        if wanted == "dest":
            self._view_activated(self.dest)
        elif wanted != "source":
            raise ValueError(tr("Valor no válido para STATE: {value}", value=value))

    def _set_output(self, value: str) -> None:
        self._set_utility("log,toggle" if value.lower().startswith("toggle") else ("log" if _turn(value, False) else "off"))

    def _set_checkboxmode(self, value: str) -> None:
        for view in self.all_views():
            view.set_check_mode(_turn(value, view.model.check_mode))

    def _set_darkmode(self, value: str) -> None:
        dark = theme.current().name != "light"  # con «según el escritorio», se cambia respecto al que se ve
        self.config["theme"] = "dark" if _turn(value, dark) else "light"
        self.apply_theme()

    def _set_expandablefolders(self, value: str) -> None:
        new = _turn(value, self.config.get("expandable_folders", True))
        self.config["expandable_folders"] = new
        for view in self.all_views():
            view.set_expandable(new)

    def _set_friendlydates(self, value: str) -> None:
        mode = value.lower()
        if mode not in ("off", "today", "normal"):
            raise ValueError(tr("Valor no válido para FRIENDLYDATES: {value}", value=value))
        self.config["friendly_dates"] = mode
        for view in self.all_views():
            view.model.set_friendly_dates(mode)

    def _set_gridlinesh(self, value: str) -> None:
        for view in self.all_views():
            view.set_grid_lines(_turn(value, view.grid_lines))

    def _set_showeverything(self, value: str) -> None:
        self._set_hidden(value)
        self.source.filter_edit.clear()
        self.source.model.set_content_types(())
        self._update_roles()

    def _set_globalhidehidden(self, value: str) -> None:
        self._set_hidden(value)

    # ---------------------------------------------------------------- imágenes
    def _cmd_image(self, cmd) -> None:
        paths = [e.path for e in self.source.selected_entries()
                 if e.extension in IMAGE_EXTENSIONS and os.path.isfile(e.path)]
        if not paths:
            self.statusBar().showMessage(tr("Selecciona una o más imágenes"), 4000)
            return
        fmt = None
        converting = cmd.has("CONVERT") or "CONVERT" in cmd.values
        if converting:
            chosen = cmd.get("CONVERT") or self._ask_format()
            if not chosen:
                return
            fmt = image_ops.normalize_format(chosen)
        rotate = int(cmd.get("ROTATE", "0"))
        width, height = _optional_int(cmd.get("WIDTH")), _optional_int(cmd.get("HEIGHT"))
        if not converting and not rotate and not width and not height:
            raise ValueError(tr("Image necesita CONVERT, ROTATE, WIDTH o HEIGHT"))
        replace = cmd.has("REPLACE")
        if replace and not self._confirm(tr("¿Sustituir {count} imágenes originales?\nEsta acción no se puede deshacer.", count=len(paths)), True):
            return
        dest_dir = self.source.path if cmd.has("HERE") else self._writable_target()
        if archive.split_archive_path(dest_dir):
            raise OSError(tr("El destino está dentro de un comprimido"))
        self.jobs.start(tr("Procesar imágenes"), lambda sink: image_ops.process_images(
            paths, dest_dir, fmt, rotate, width, height, cmd.has("PRESERVEASPECTRATIO") or not (width and height),
            replace, cmd.has("ADDSUFFIX"), cmd.has("PRESERVEDATE"), sink),
            lambda r: self._push_created(r) if not replace else None)

    def _ask_format(self) -> str | None:
        formats = image_ops.writable_formats()
        default = formats.index("png") if "png" in formats else 0
        text, ok = QInputDialog.getItem(self, tr("Convertir imagen"), tr("Formato:"), formats, default, False)
        return text if ok else None

    def _push_created(self, result: OpResult) -> None:
        created = [dest for _src, dest, is_new in result.placed if is_new]
        if created:
            self._push_undo({"kind": "created", "paths": created})

    # ---------------------------------------------------------------- dividir y unir
    def _cmd_split(self, _cmd) -> None:
        files = [e.path for e in self.source.selected_entries() if not e.is_dir and os.path.isfile(e.path)]
        if len(files) != 1:
            self.statusBar().showMessage(tr("Selecciona un único archivo para dividir"), 4000)
            return
        size = self._ask_part_size()
        if not size:
            return
        path = files[0]
        target_dir = self._writable_target()
        self.jobs.start(tr("Dividir {basename}", basename=os.path.basename(path)), lambda sink: fsops.split_file(path, target_dir, size, sink))

    def _ask_part_size(self) -> int | None:
        megabytes, ok = QInputDialog.getInt(self, tr("Dividir archivo"), tr("Tamaño de cada parte (MB):"), 100, 1, 10 ** 7)
        return megabytes * MEGABYTE if ok else None

    def _cmd_join(self, _cmd) -> None:
        selected = self.source.selected_paths()
        parts = fsops.split_sequence(selected[0]) if len(selected) == 1 else sorted(selected)
        if len(parts) < 2 or any(not os.path.isfile(p) for p in parts):
            self.statusBar().showMessage(tr("Selecciona la primera parte (.001) o todas las partes"), 4000)
            return
        stem = os.path.basename(os.path.splitext(parts[0])[0])
        target_dir = self._writable_target()
        target = fsops.unique_name(target_dir, stem)
        self.jobs.start(tr("Unir en {basename}", basename=os.path.basename(target)), lambda sink: fsops.join_files(parts, target, sink),
                        self._push_created)

    # ---------------------------------------------------------------- barras
    def _named_bars(self) -> dict[str, QToolBar]:
        # El menú queda fuera a propósito: ocultarlo dejaría sin forma de volver a mostrar nada.
        return {"favorites bar": self.favorites_bar, "toolbar": self.toolbar, "tabbar": self.style_bar,
                "pathbar": self.location_bar}

    def _cmd_toolbar(self, cmd) -> None:
        if cmd.has("RESETDEFAULTS"):
            self._reset_toolbar()
        elif cmd.has("LIST"):
            menu = QMenu(self)
            for label, bar in ((tr("Barra de herramientas"), self.toolbar), (tr("Estilos"), self.style_bar),
                               (tr("Localización"), self.location_bar), (tr("Barra de favoritos"), self.favorites_bar)):
                action = menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(not bar.isHidden())
                action.toggled.connect(bar.setVisible)
            menu.exec(self.cursor().pos())
        elif cmd.get("NAME"):
            bar = self._named_bars().get(cmd.get("NAME").casefold())
            if bar is None:
                raise ValueError(tr("Barra desconocida: {get}", get=cmd.get('NAME')))
            bar.setVisible(bar.isHidden() if cmd.has("TOGGLE") else True)
            if bar is self.favorites_bar:
                self.config["show_favorites_bar"] = not bar.isHidden()
        else:
            raise ValueError(tr("Toolbar necesita NAME, LIST o RESETDEFAULTS"))

    def _rebuild_favorites_bar(self) -> None:
        nodes = favorites.clean(self.config.get("favorites", []))[0]
        for tree in self.all_trees():
            tree.set_favorites(nodes)
        bar = self.favorites_bar
        bar.clear()
        on_bar = favorites.bar_items(nodes)
        if not on_bar:
            bar.addAction(tr("Arrastra favoritos a la rama «Barra de favoritos» en Editar Favoritos")).setEnabled(False)
        for node in on_bar:
            self._add_favorites_bar_item(node)
        others = favorites.other_items(nodes)
        if others:
            spacer = QWidget()
            spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            bar.addWidget(spacer)
            self._add_favorites_bar_menu("favorites", tr("Otros Favoritos"), others)

    def _add_favorites_bar_item(self, node: dict) -> None:
        bar = self.favorites_bar
        what = favorites.kind(node)
        if what == "separator":
            bar.addSeparator()
        elif what == "space":
            spacer = QWidget()
            spacer.setFixedWidth(12)
            bar.addWidget(spacer)
        elif what == "header":
            label = QLabel(node["header"])
            label.setObjectName("barLabel")
            bar.addWidget(label)
        elif what == "branch":
            self._add_favorites_bar_menu("folder", node["label"], node["children"])
        elif what in ("folder", "file"):
            path = node["path"]
            action = QAction(themed("file-kind" if node.get("file") else "folder"), node["label"], self)
            action.setToolTip(location_label(path))
            action.setEnabled(path == COMPUTER_PATH or os.path.exists(path))
            action.triggered.connect(lambda _=False, n=node: self._open_favorite(n, self.source))
            bar.addAction(action)
            bar.widgetForAction(action).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

    def _add_favorites_bar_menu(self, icon: str, text: str, nodes: list) -> None:
        action = QAction(themed(icon), text, self)
        menu = QMenu(self)

        def fill() -> None:
            menu.clear()
            self._add_favorite_items(menu, nodes, path_states([node["path"] for node in favorites.walk(nodes)]))

        menu.aboutToShow.connect(fill)
        action.setMenu(menu)
        self.favorites_bar.addAction(action)
        button = self.favorites_bar.widgetForAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    # ---------------------------------------------------------------- recientes y favoritos
    def _set_fullrowselect(self, value: str) -> None:
        new = _turn(value, bool(self.config.get("full_row_select", False)))
        self.config["full_row_select"] = new
        for view in self.all_views():
            view.set_full_row(new)

    def _remember_recent(self, path: str) -> None:
        settings = frequent.normalize_recent(self.config.get("recent_list"))
        if not settings["enabled"] or (frequent.is_virtual(path) and not settings["virtual"]):
            return
        recent = self.config.get("recent", [])
        now = time.monotonic()
        passing, entered = getattr(self, "_recent_entered", ("", 0.0))
        if passing and passing != path and now - entered < frequent.TRANSIT_SECONDS and _is_inside(path, passing):
            # Se entró en una subcarpeta enseguida y sin hacer nada: en la anterior solo se estaba de paso.
            self._award("transit", passing)
            if settings["skip_transit"]:
                recent = [item for item in recent if item.get("path") != passing]
        recent = [item for item in recent if item.get("path") != path]
        recent.insert(0, {"path": path})
        del recent[settings["max"]:]
        self.config["recent"] = recent
        self._recent_entered = (path, now)

    def _award(self, action: str, path: str | None = None) -> None:
        """Suma los puntos de una acción. Hacer algo en una carpeta también significa que no se estaba solo de paso."""
        self._recent_entered = ("", 0.0)
        frequent.award(self.config, path or self.source.path, action)

    def _award_command(self, cmd) -> None:
        name, source = cmd.name, self.source
        selected = bool(source.selected_paths())
        if name == "copy" and selected:
            moving = cmd.has("MOVE")
            self._award("move_from" if moving else "copy_from")
            target = source.path if cmd.has("DUPLICATE") or cmd.has("HERE") else cmd.get("TO") or (self.dest.path if self.dest else None)
            if target:
                self._award("move_to" if moving else "copy_to", target)
        elif name == "clipboard":
            for argument, action in (("COPY", "clip_copy"), ("CUT", "clip_cut"), ("PASTE", "paste")):
                if cmd.has(argument) and (selected or argument == "PASTE"):
                    self._award(action)
        elif name in COMMAND_POINTS and (selected or name not in NEED_SELECTION):
            self._award(COMMAND_POINTS[name])

    def _cmd_recent(self, cmd) -> None:
        if cmd.has("CLEAR"):
            self.config["recent"] = []
            self.statusBar().showMessage(tr("Lista de recientes vacía"), 3000)
            return
        self._popup_paths(self.config.get("recent", []), tr("No hay carpetas recientes"))

    def _popup_paths(self, items: list[dict], empty_message: str) -> None:
        existing = [i.get("path", "") for i in items if i.get("path") == COMPUTER_PATH or os.path.isdir(i.get("path", ""))]
        if not existing:
            self.statusBar().showMessage(empty_message, 4000)
            return
        menu = QMenu(self)
        for path in existing:
            action = menu.addAction(themed("folder"), location_label(path))
            action.triggered.connect(lambda _=False, p=path: self.source.navigate(p))
        menu.exec(self.cursor().pos())

    def smart_favorites(self) -> list[dict]:
        """Carpetas más usadas, aprendidas solo de esta instalación. Las que ya son favoritas no se repiten."""
        return frequent.shown(self.config)

    def _check_smart_folders(self) -> None:
        """Al arrancar, olvida las carpetas que ya no existen. En segundo plano: puede haber unidades de red lentas."""
        settings = frequent.normalize_smart(self.config.get("smart_favorites"))
        table = self.config.get("smart_points")
        if not (settings["enabled"] and settings["check_startup"] and isinstance(table, dict) and table):
            return
        paths = list(table)

        def forget(gone: list[str]) -> None:
            for path in gone:
                table.pop(path, None)

        run_bg(lambda _sink: frequent.gone_folders(paths), forget)

    def _edit_favorites(self) -> None:
        self.open_preferences(favorites_page=True)

    # ---------------------------------------------------------------- diseños y grupos de pestañas
    def _snapshot(self) -> dict:
        return {"tabs": [[v.path for v in group.views()] for group in self.groups], "dual": self.dual,
                "style": self.config.get("style", "")}

    def _load_tabs(self, tabs: list, replace: bool = True) -> None:
        """Abre las pestañas guardadas. Las rutas que ya no existen se omiten, nunca se inventan."""
        for side, paths in enumerate(tabs[:len(self.groups)]):
            valid = [p for p in paths if isinstance(p, str) and (os.path.isdir(p) or p == COMPUTER_PATH)]
            if not valid:
                continue
            group = self.groups[side]
            old = group.views() if replace else []
            for path in valid:
                group.add_tab(path, activate=False)
            for view in old:
                group.close_tab(group.indexOf(view))

    def save_layout(self, name: str) -> None:
        self.config.setdefault_dict("layouts")[name] = self._snapshot()

    def load_layout(self, name: str) -> None:
        layout = self.config.get("layouts", {}).get(name)
        if not layout:
            raise ValueError(tr("Diseño desconocido: {name}", name=name))
        if self._style_named(layout.get("style", "")):
            self.apply_style(layout["style"])
        self.set_dual(layout.get("dual", self.dual))
        self._load_tabs(layout.get("tabs", []))
        self._update_roles()

    def _popup_named(self, names: list[str], action, empty_message: str, icon: str = "single") -> None:
        if not names:
            self.statusBar().showMessage(empty_message, 4000)
            return
        menu = QMenu(self)
        for name in names:
            item = menu.addAction(themed(icon), name)
            item.triggered.connect(lambda _=False, n=name: action(n))
        menu.exec(self.cursor().pos())

    def _delete_layout(self, name: str) -> None:
        if self._confirm(tr("¿Eliminar el diseño «{name}»?", name=name)):
            self.config.get("layouts", {}).pop(name, None)

    def _save_tab_group(self) -> None:
        name = self._ask_text(tr("Guardar grupo de pestañas"), tr("Nombre:"), "")
        if name:
            self.save_tab_group(name)

    def save_tab_group(self, name: str) -> None:
        self.config.setdefault_dict("tabgroups")[name] = [[v.path for v in group.views()] for group in self.groups]

    def load_tab_group(self, name: str) -> None:
        tabs = self.config.get("tabgroups", {}).get(name)
        if tabs is None:
            raise ValueError(tr("Grupo de pestañas desconocido: {name}", name=name))
        self._load_tabs(tabs)

    # ---------------------------------------------------------------- importar y exportar
    def export_favorites_file(self, path: str) -> int:
        nodes = self.config.get("favorites", [])
        settings_io.export_favorites(path, nodes)
        return sum(1 for _node in favorites.walk(nodes))

    def import_favorites_file(self, path: str) -> tuple[int, int]:
        """Añade los favoritos que aún no estén. Devuelve (añadidos, descartados por no ser válidos)."""
        found, skipped = settings_io.read_favorites(path)
        nodes = self.config.get("favorites", [])
        known = {node["path"] for node in favorites.walk(nodes)}
        added = _only_new(found, known)
        self.config["favorites"] = favorites.clean(list(nodes) + added)[0]
        self._rebuild_favorites_bar()
        return sum(1 for _node in favorites.walk(added)), skipped

    def _export_favorites_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Exportar favoritos"), "favoritos.json", "JSON (*.json)")
        if path:
            count = self.export_favorites_file(path)
            self.statusBar().showMessage(tr("{count} favoritos exportados", count=count), 5000)

    def _import_favorites_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Importar favoritos"), "", "JSON (*.json)")
        if not path:
            return
        added, skipped = self.import_favorites_file(path)
        note = tr(" Se descartaron {skipped} no válidos.", skipped=skipped) if skipped else ""
        QMessageBox.information(self, APP_NAME, tr("Se añadieron {added} favoritos.{note}", added=added, note=note))

    def export_settings_file(self, path: str) -> None:
        self.save_session()
        settings_io.export_settings(path, self.config.data)

    def import_settings_file(self, path: str) -> list[str]:
        """Aplica un archivo de configuración al momento. Devuelve los nombres de lo descartado."""
        settings, ignored = settings_io.read_settings(path)
        if settings:
            self.config.data.update(settings)
            self.apply_settings()
            self.config.save()
        return ignored

    def apply_settings(self) -> None:
        """Lleva la configuración actual a la ventana, sin tener que reiniciar el programa."""
        config = self.config
        self.apply_theme()
        self.apply_tab_settings()
        self._apply_level_colors()
        self._rebuild_favorites_bar()
        self.favorites_bar.setVisible(bool(config.get("show_favorites_bar", False)))
        self._apply_bar_lock()
        self._set_hidden("on" if config.get("show_hidden", False) else "off")
        self._set_expandablefolders("on" if config.get("expandable_folders", True) else "off")
        self._set_friendlydates(config.get("friendly_dates", "off"))
        self._set_fullrowselect("on" if config.get("full_row_select", False) else "off")
        self._set_hoverinfo("on" if config.get("hover_info", True) else "off")
        if not self._style_named(config.get("style", "")):
            styles = config.get("styles", [])
            config["style"] = styles[0]["name"] if styles else ""
        if config.get("style"):
            self.apply_style(config["style"])

    def _export_settings_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Exportar configuración"), "configuracion.json", "JSON (*.json)")
        if path:
            self.export_settings_file(path)
            self.statusBar().showMessage(tr("Configuración exportada"), 5000)

    def _import_settings_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Importar configuración"), "", "JSON (*.json)")
        if not path:
            return
        # Los botones de una barra pueden ejecutar programas con `Run`.
        if not self._confirm(tr("¿Importar esta configuración?\nSustituye los ajustes que contenga el archivo. Sus botones pueden ejecutar programas: importa solo archivos de confianza."), True):
            return
        ignored = self.import_settings_file(path)
        note = tr("\nSe descartó lo que no era válido: {join}.", join=', '.join(ignored)) if ignored else ""
        QMessageBox.information(self, APP_NAME, tr("Configuración importada.{note}", note=note))

    # ---------------------------------------------------------------- comandos sueltos
    def _cmd_find(self, _cmd) -> None:
        self._set_utility("find")

    def _cmd_play(self, _cmd) -> None:
        for entry in self.source.selected_entries():
            if not entry.is_dir:
                self._open_file(self.source, entry)

    def _cmd_print(self, cmd) -> None:
        text = listing_text(self.source.model.entries)
        if (cmd.get("TO") or "").lower() == "clip":
            QApplication.clipboard().setText(text)
            self.statusBar().showMessage(tr("Listado copiado al portapapeles"), 3000)
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Guardar el listado de la carpeta"), "listado.txt", tr("Texto (*.txt)"))
        if path:
            with open(path, "w", encoding="utf-8") as fout:
                fout.write(text)

    def _create_empty_zip(self, name: str | None) -> None:
        name = name or self._ask_text(tr("Nuevo comprimido"), tr("Nombre del ZIP:"), tr("Nuevo.zip"))
        if not name:
            return
        reason = fsops.validate_name(name)
        if reason:
            raise ValueError(reason)
        target = fsops.unique_name(self.source.path, name if name.lower().endswith(".zip") else name + ".zip")
        with zipfile.ZipFile(target, "w"):
            pass
        self._push_undo({"kind": "created", "paths": [target]})
        self.source.navigate(self.source.path, focus=target)

    def _new_file_menu(self) -> None:
        menu = QMenu(self)
        for label, extension in ((tr("Archivo de texto"), ".txt"), (tr("Documento Markdown"), ".md"), (tr("Archivo vacío"), "")):
            menu.addAction(themed("newfile"), label, lambda e=extension: self.run_command(f"FileType NEW={e}"))
        menu.exec(self.cursor().pos())

    def _open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _copy_update(self, paths: list[str], target: str, move: bool, policy: Collision) -> None:
        self.jobs.start(tr("Copiar lo más reciente"), lambda sink: fsops.transfer(paths, target, move, policy, sink), None,
                        (tr("Moviendo") if move else tr("Copiando"), os.path.dirname(paths[0]), target))

    def _duplicate_with_template(self, paths: list[str], template: str, ignore_ext: bool) -> None:
        """`Copy DUPLICATE AS "* - fecha"`: cada copia toma el nombre del original."""
        from ..core.commands import expand_date_codes

        def new_name(path: str) -> str:
            name = os.path.basename(path)
            stem, ext = os.path.splitext(name) if ignore_ext else (name, "")
            return expand_date_codes(template).replace("*", stem) + ext

        def work(sink):
            total = OpResult()
            for path in paths:
                sink.check()
                part = fsops.transfer([path], os.path.dirname(path), False, Collision.RENAME, sink, new_name(path))
                total.done += part.done
                total.errors.extend(part.errors)
                total.placed.extend(part.placed)
                total.cancelled = total.cancelled or part.cancelled
            return total

        self.jobs.start(tr("Duplicar"), work, self._push_created)

    def _zip_each(self, paths: list[str], target_dir: str) -> None:
        """`Copy ARCHIVE=single`: un ZIP por cada elemento."""
        def work(sink):
            result = OpResult()
            for path in paths:
                sink.check()
                target = fsops.unique_name(target_dir, os.path.basename(path.rstrip("/\\")) + ".zip")
                try:
                    archive.create_zip([path], target, lambda _n: sink.check())
                    result.done += 1
                    result.placed.append((path, target, True))
                except OSError as exc:
                    result.errors.append((path, str(exc)))
            return result

        self.jobs.start(tr("Comprimir por separado"), work, self._push_created)

    def _select_similar(self) -> None:
        view = self.source
        selected = view.selected_entries()
        extensions = {e.extension for e in selected if not e.is_dir}
        folders = any(e.is_dir for e in selected)
        view.select_paths([e.path for e in view.model.entries
                           if (e.is_dir and folders) or (not e.is_dir and e.extension in extensions)])

    def _select_between(self, cmd) -> None:
        """Compara con el otro panel por nombre. `noext` ignora la extensión; `DESELECTNOMATCH` solo reduce."""
        other = self.dest
        if other is None:
            return
        view = self.source
        key = "SOURCETODEST" if "SOURCETODEST" in cmd.values or cmd.has("SOURCETODEST") else "DESTTOSOURCE"
        options = {o.strip().lower() for o in (cmd.get(key) or "in").split(",")}
        ignore_ext = "noext" in options
        present = "notin" not in options

        def name_of(entry) -> str:
            name = entry.name.casefold()
            return os.path.splitext(name)[0] if ignore_ext else name

        if key == "DESTTOSOURCE":
            view, other = other, view
        names = {name_of(e) for e in other.model.entries}
        pool = view.selected_entries() if cmd.has("DESELECTNOMATCH") else view.model.entries
        view.select_paths([e.path for e in pool if (name_of(e) in names) == present])

    def _drives_menu_popup(self) -> None:
        menu = QMenu(self)
        for volume in QStorageInfo.mountedVolumes():
            if volume.isValid() and volume.isReady():
                root = volume.rootPath()
                action = menu.addAction(themed("drive"), f"{volume.displayName() or root} ({root})")
                action.triggered.connect(lambda _=False, p=root: self.source.navigate(p))
        menu.exec(self.cursor().pos())


def _optional_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(tr("Número no válido: {value}", value=value)) from exc
    if number <= 0:
        raise ValueError(tr("Debe ser mayor que cero: {value}", value=value))
    return number
