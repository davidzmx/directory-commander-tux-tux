"""Qué muestran los menús de clic derecho y en qué orden. Sin Qt, para poder probarlo sin interfaz."""
from __future__ import annotations

from .i18n import N_

SEPARATOR = "---"
SHELL_ITEMS = "@otros-programas"
SHELL_ITEMS_LABEL = N_("Opciones de otros programas")
FOLDER = "folder"
FILE = "file"
OPEN_WITH = N_("Abrir con")
BROWSE_ARCHIVE = N_("Explorar contenido")

DEFAULT_LAYOUTS: dict[str, list[str]] = {
    FOLDER: [N_("Pegar"), SEPARATOR,
             N_("Nueva carpeta"), N_("Nuevo archivo de texto"), N_("Nuevo ZIP vacío"), SEPARATOR,
             N_("Ver"), N_("Ordenar por"), N_("Vista plana"), N_("Mostrar ocultos"), SEPARATOR,
             N_("Seleccionar todo"), N_("Actualizar"), N_("Calcular tamaños de carpetas"), SEPARATOR,
             N_("Abrir en una pestaña nueva"), N_("Dividir panel - Horizontal"), N_("Dividir panel - Vertical"), N_("Cerrar este panel"), N_("Abrir terminal aquí"), N_("Añadir a favoritos"), N_("Copiar la ruta de esta carpeta"), SEPARATOR,
             SHELL_ITEMS, SEPARATOR,
             N_("Propiedades de esta carpeta")],
    FILE: [N_("Abrir"), OPEN_WITH, BROWSE_ARCHIVE, N_("Abrir en una pestaña nueva"), N_("Ver imágenes"), SEPARATOR,
           N_("Copiar"), N_("Cortar"), SEPARATOR,
           N_("Copiar a destino"), N_("Mover a destino"), N_("Duplicar aquí"), SEPARATOR,
           N_("Renombrar"), N_("Enviar a la papelera"), N_("Eliminar definitivamente"), SEPARATOR,
           N_("Extraer"), N_("Comprimir en ZIP…"), N_("Imagen"), N_("Calcular hash"), N_("Calcular tamaño"), SEPARATOR,
           SHELL_ITEMS, SEPARATOR,
           N_("Copiar rutas como texto"), N_("Atributos…"), N_("Propiedades")],
}


def _strings(value) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def normalize_layout(raw, which: str) -> dict:
    """Orden y elementos ocultos de un menú. Lo que no se conoce se descarta, y lo que falta vuelve a su sitio:
    así, una opción nueva del programa aparece aunque la configuración se guardara antes de existir."""
    source = raw if isinstance(raw, dict) else {}
    defaults = DEFAULT_LAYOUTS[which]
    known = set(defaults) - {SEPARATOR}
    order: list[str] = []
    for key in _strings(source.get("order")):
        if key == SEPARATOR or (key in known and key not in order):
            order.append(key)
    if not order:
        order = list(defaults)
    else:
        for position, key in enumerate(defaults):
            if key == SEPARATOR or key in order:
                continue
            previous = next((k for k in reversed(defaults[:position]) if k != SEPARATOR and k in order), None)
            order.insert(order.index(previous) + 1 if previous else 0, key)
    return {"order": order, "hidden": [key for key in dict.fromkeys(_strings(source.get("hidden"))) if key in known]}


def normalize(raw) -> dict:
    source = raw if isinstance(raw, dict) else {}
    hidden = source.get("shell_hidden")
    return {FOLDER: normalize_layout(source.get(FOLDER), FOLDER),
            FILE: normalize_layout(source.get(FILE), FILE),
            # Clave de la opción y nombre para mostrarlo en las preferencias, aunque ahora no esté en el menú.
            "shell_hidden": {k: v for k, v in hidden.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(hidden, dict) else {}}


def arranged_keys(layout: dict, present: set[str]) -> list[str]:
    """Lo que hay que mostrar, en orden y sin separadores sobrantes. `present` son las opciones que tienen sentido ahora."""
    hidden = set(layout["hidden"])
    result: list[str] = []
    for key in layout["order"]:
        if key == SEPARATOR:
            if result and result[-1] != SEPARATOR:
                result.append(key)
        elif key not in hidden and (key in present or key == SHELL_ITEMS):
            result.append(key)
    while result and result[-1] == SEPARATOR:
        result.pop()
    return result
