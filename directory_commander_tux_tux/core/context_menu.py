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
THUMBNAIL_SIZE = N_("Tamaño de las miniaturas")

DEFAULT_LAYOUTS: dict[str, list[str]] = {
    FOLDER: [N_("Pegar"), SEPARATOR,
             N_("Nueva carpeta"), N_("Nuevo archivo de texto"), N_("Nuevo ZIP vacío"), SEPARATOR,
             N_("Ver"), THUMBNAIL_SIZE, N_("Ordenar por"), N_("Vista plana"), N_("Mostrar ocultos"), SEPARATOR,
             N_("Seleccionar todo"), N_("Actualizar"), N_("Calcular tamaños de carpetas"), N_("Uso del disco…"), SEPARATOR,
             N_("Abrir en una pestaña nueva"), N_("Dividir panel - Horizontal"), N_("Dividir panel - Vertical"), N_("Cerrar este panel"), N_("Solo la terminal en este panel"), N_("Panel remoto (SFTP)…"), N_("Desconectar del servidor"), N_("Abrir terminal aquí"), N_("Añadir a favoritos"), N_("Copiar la ruta de esta carpeta"), SEPARATOR,
             SHELL_ITEMS, SEPARATOR,
             N_("Propiedades de esta carpeta")],
    FILE: [N_("Abrir"), OPEN_WITH, N_("Editar Markdown"), N_("Ver JSON"), BROWSE_ARCHIVE, N_("Abrir en una pestaña nueva"), N_("Añadir a favoritos"), N_("Ver imágenes"), THUMBNAIL_SIZE, SEPARATOR,
           N_("Copiar"), N_("Copiar rutas como texto"), N_("Cortar"), SEPARATOR,
           N_("Copiar a destino"), N_("Mover a destino"), N_("Duplicar aquí"), SEPARATOR,
           N_("Renombrar"), N_("Enviar a la papelera"), N_("Eliminar definitivamente"), SEPARATOR,
           N_("Extraer"), N_("Comprimir en ZIP…"), N_("Imagen"), N_("Calcular hash"), N_("Calcular tamaño"), SEPARATOR,
           SHELL_ITEMS, SEPARATOR,
           N_("Atributos…"), N_("Propiedades")],
}


# Los menús de serie de versiones anteriores. Preferencias guarda el menú entero aunque no se haya cambiado nada: uno que es
# exactamente uno de estos se toma como el de serie de ahora, para que reciba las opciones nuevas en su sitio.
_PREVIOUS_DEFAULTS: dict[str, list[list[str]]] = {
    FOLDER: [
        ["Pegar", SEPARATOR, "Nueva carpeta", "Nuevo archivo de texto", "Nuevo ZIP vacío", SEPARATOR,
         "Ver", "Ordenar por", "Vista plana", "Mostrar ocultos", SEPARATOR,
         "Seleccionar todo", "Actualizar", "Calcular tamaños de carpetas", "Uso del disco…", SEPARATOR,
         "Abrir en una pestaña nueva", "Dividir panel - Horizontal", "Dividir panel - Vertical", "Cerrar este panel",
         "Solo la terminal en este panel", "Panel remoto (SFTP)…", "Desconectar del servidor", "Abrir terminal aquí",
         "Añadir a favoritos", "Copiar la ruta de esta carpeta", SEPARATOR, SHELL_ITEMS, SEPARATOR,
         "Propiedades de esta carpeta"],
    ],
    FILE: [
        ["Abrir", OPEN_WITH, BROWSE_ARCHIVE, "Abrir en una pestaña nueva", "Ver imágenes", SEPARATOR, "Copiar", "Cortar", SEPARATOR,
         "Copiar a destino", "Mover a destino", "Duplicar aquí", SEPARATOR, "Renombrar", "Enviar a la papelera", "Eliminar definitivamente", SEPARATOR,
         "Extraer", "Comprimir en ZIP…", "Imagen", "Calcular hash", "Calcular tamaño", SEPARATOR, SHELL_ITEMS, SEPARATOR,
         "Copiar rutas como texto", "Atributos…", "Propiedades"],
        ["Abrir", OPEN_WITH, "Editar Markdown", "Ver JSON", BROWSE_ARCHIVE, "Abrir en una pestaña nueva", "Añadir a favoritos", "Ver imágenes", SEPARATOR,
         "Copiar", "Copiar rutas como texto", "Cortar", SEPARATOR,
         "Copiar a destino", "Mover a destino", "Duplicar aquí", SEPARATOR,
         "Renombrar", "Enviar a la papelera", "Eliminar definitivamente", SEPARATOR,
         "Extraer", "Comprimir en ZIP…", "Imagen", "Calcular hash", "Calcular tamaño", SEPARATOR,
         SHELL_ITEMS, SEPARATOR,
         "Atributos…", "Propiedades"],
    ],
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
    stored = _strings(source.get("order"))
    if stored in _PREVIOUS_DEFAULTS.get(which, []):
        stored = list(defaults)
    for key in stored:
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
